"""The eval dataset, run as pydantic-evals experiments (they appear under Experiments in Logfire).

    python evals/experiment.py                                    # both providers' configured models
    python evals/experiment.py --models anthropic:claude-haiku-4-5 gemini:gemini-3.8-flash --repeat 3
    python evals/experiment.py --patients loyd638 aaron697        # a subset, for a quick look

Run from the repo root so the one top-level .env is found. Each model is one experiment on the
same dataset; every case runs `--repeat` times, because a model's answer is not deterministic.

What Logfire stores is de-identified by construction: a case's input is only its patient key,
its output is the packet as the model wrote it (placeholders and shifted dates), and the case
name uses a short id, never a patient's name.
"""

import argparse
import asyncio
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import logfire
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from app.config import settings
from app.llm.client import LLMResult, build_client
from app.observability import estimate_cost, setup_observability
from dataset import GroundTruth, open_ground_truth
from run import RUNS_DIR, CaseResult, connect, run_case

DATASET_NAME = "prior-auth-adalimumab-ra"
CASES_PER_STRATUM = 2  # the smallest and the largest chart in each stratum


@dataclass
class PatientInput:
    patient_id: str


@dataclass
class Expected:
    """What a correct packet says, per criterion, derived from ground truth (never typed by hand,
    so changing a rule such as how a completed order is treated relabels every case)."""

    kinds: dict[str, str]  # criterion id -> "evidence" | "gap"


# --- the dataset --------------------------------------------------------------------------------

def stratum_of(truth: GroundTruth, patient_id: str) -> str:
    chart = truth.store.chart(patient_id)
    has_order = any(m.code.code == "105585" for m in chart.medication_requests)
    trial = ("trial-shown" if truth.established(patient_id, "dmard_trial")
             else "trial-not-shown" if has_order else "no-order")
    notes = "active-notes" if truth.established(patient_id, "active_disease") else "no-active-notes"
    ra = "ra-active" if truth.established(patient_id, "ra_diagnosis") else "ra-not-active"
    return f"{ra}/{trial}/{notes}"


def select_patients(truth: GroundTruth) -> list[tuple[str, str]]:
    """(patient_id, stratum): from each situation the rules distinguish, the smallest and the
    largest chart, so the note-reading cap is exercised as well as the easy cases."""
    groups: dict[str, list[tuple[int, str]]] = {}
    for case in truth.cases():
        groups.setdefault(stratum_of(truth, case.patient_id), []).append(
            (len(truth.store.chart(case.patient_id).documents), case.patient_id)
        )
    chosen = []
    for stratum in sorted(groups):
        ordered = sorted(groups[stratum])
        picks = {ordered[0][1], ordered[-1][1]} if CASES_PER_STRATUM >= 2 else {ordered[0][1]}
        chosen += [(pid, stratum) for _, pid in ordered if pid in picks]
    return chosen


def build_dataset(truth: GroundTruth, patient_ids: list[str] | None = None) -> Dataset:
    # Named patients always run, selected or not; the default is the stratified selection.
    chosen = ([(p, stratum_of(truth, p)) for p in patient_ids] if patient_ids is not None
              else select_patients(truth))
    cases = []
    for patient_id, stratum in chosen:
        cases.append(
            Case(
                name=f"{stratum} · {patient_id[:8]}",
                inputs=PatientInput(patient_id),
                expected_output=Expected(
                    {cid: "evidence" if truth.established(patient_id, cid) else "gap"
                     for cid in truth.criterion_ids}
                ),
                metadata={"stratum": stratum, "notes": len(truth.store.chart(patient_id).documents)},
            )
        )
    return Dataset(
        name=DATASET_NAME,
        cases=cases,
        evaluators=[PipelineCompleted(), ResolutionScores(), DecisionsCorrect(), IdsWellFormed(),
                    VerifyMatchesTruth()],
    )


# --- evaluators ---------------------------------------------------------------------------------
# A bool becomes a pass/fail assertion in Logfire, a float a score. A run that failed to
# complete gets no scores at all, only `completed = False`, so failures cannot hide in averages.

Ctx = EvaluatorContext[PatientInput, CaseResult, dict]


@dataclass
class PipelineCompleted(Evaluator[PatientInput, CaseResult, dict]):
    def evaluate(self, ctx: Ctx) -> dict[str, bool]:
        return {"completed": ctx.output.ok}


@dataclass
class ResolutionScores(Evaluator[PatientInput, CaseResult, dict]):
    """The citation-resolution metrics, scored against ground truth by evals/metrics."""

    def evaluate(self, ctx: Ctx) -> dict[str, float]:
        out = ctx.output
        scores = {
            "citation_resolution_rate": out.citation_resolution_rate,
            "citation_level_rate": out.citation_level_rate,
            "gap_accuracy": out.gap_accuracy,
        }
        return {k: v for k, v in scores.items() if out.ok and v is not None}


@dataclass
class DecisionsCorrect(Evaluator[PatientInput, CaseResult, dict]):
    """Did the model make the right call on each criterion, and is every claim it made sound?"""

    def evaluate(self, ctx: Ctx) -> dict[str, bool]:
        out = ctx.output
        if not out.ok:
            return {}
        chosen = {a["criterion_id"]: a["kind"] for a in out.assertions}
        return {
            "decisions_correct": chosen == ctx.expected_output.kinds,
            "packet_fully_correct": chosen == ctx.expected_output.kinds
            and all(a["truth_resolved"] for a in out.assertions) and not out.unaddressed,
        }


@dataclass
class IdsWellFormed(Evaluator[PatientInput, CaseResult, dict]):
    """Did the model write every id exactly as given? A repaired id still resolves, so this is
    reported separately from correctness: it is a reliability signal, not a truthfulness one."""

    def evaluate(self, ctx: Ctx) -> dict[str, bool]:
        return {"ids_well_formed": ctx.output.citations_repaired == 0} if ctx.output.ok else {}


@dataclass
class VerifyMatchesTruth(Evaluator[PatientInput, CaseResult, dict]):
    """The harness's own check: the runtime `verify` node and the offline ground truth must
    agree on every assertion. A failure here is a bug in verify or extract, not the model."""

    def evaluate(self, ctx: Ctx) -> dict[str, bool]:
        return {"verify_matches_truth": bool(ctx.output.ok and ctx.output.verify_agrees_with_truth)}


# --- running ------------------------------------------------------------------------------------

def make_task(session, llm, truth: GroundTruth):
    async def task(inputs: PatientInput) -> CaseResult:
        result, _ = await run_case(session, llm, inputs.patient_id, truth, settings.as_of)
        return replace(result, patient_id=inputs.patient_id[:8])  # nothing longer goes to Logfire

    return task


def client_for(spec: str):
    provider, _, model = spec.partition(":")
    if provider not in ("anthropic", "gemini") or not model:
        raise SystemExit(f"--models entries look like provider:model, got {spec!r}")
    update = {"llm_provider": provider, ("anthropic_model" if provider == "anthropic" else "gemini_model"): model}
    return build_client(settings.model_copy(update=update)), provider, model


def summarize(spec: str, provider: str, model: str, report) -> dict:
    outputs = [c.output for c in report.cases]
    tokens_in = sum(o.input_tokens or 0 for o in outputs)
    tokens_out = sum(o.output_tokens or 0 for o in outputs)
    cost = estimate_cost(LLMResult(parsed=None, provider=provider, model=model,
                                   input_tokens=tokens_in, output_tokens=tokens_out))
    avg = report.averages()
    return {
        "experiment": spec,
        "runs": len(report.cases),
        "task_failures": len(report.failures),
        "averages": {"scores": avg.scores if avg else {}, "assertions": avg.assertions if avg else None},
        "tokens": {"input": tokens_in, "output": tokens_out},
        "estimated_cost_usd": None if cost is None else round(cost, 4),
        "cases": [
            {
                "name": c.name,
                "scores": {k: v.value for k, v in c.scores.items()},
                "assertions": {k: v.value for k, v in c.assertions.items()},
                "error": c.output.error,
            }
            for c in report.cases
        ],
    }


async def run_experiments(args, truth: GroundTruth, patient_ids: list[str] | None) -> list[dict]:
    dataset = build_dataset(truth, patient_ids)
    summaries = []
    async with connect(args.mcp_url) as session:
        for spec in args.models:
            llm, provider, model = client_for(spec)
            report = await dataset.evaluate(
                make_task(session, llm, truth),
                name=spec,
                task_name="assemble-pipeline",
                metadata={"provider": provider, "model": model, "as_of": settings.as_of.isoformat(),
                          "repeat": args.repeat},
                repeat=args.repeat,
                max_concurrency=args.concurrency,
            )
            report.print(include_input=False, include_output=False, include_averages=True)
            summaries.append(summarize(spec, provider, model, report))
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--models", nargs="+",
                        default=[f"anthropic:{settings.anthropic_model}", f"gemini:{settings.gemini_model}"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--patients", nargs="+", help="name fragments or ids; default is the whole dataset")
    parser.add_argument("--mcp-url")
    parser.add_argument("--out-dir", type=Path, default=RUNS_DIR)
    args = parser.parse_args(argv)

    mode = setup_observability(settings)
    truth = open_ground_truth(settings.as_of)
    patient_ids = None
    if args.patients:
        from run import resolve_patient
        patient_ids = [resolve_patient(truth, p) for p in args.patients]
    dataset = build_dataset(truth, patient_ids)
    print(f"dataset {DATASET_NAME}: {len(dataset.cases)} cases x {args.repeat} runs x {len(args.models)} models "
          f"= {len(dataset.cases) * args.repeat * len(args.models)} pipeline runs; as of {settings.as_of}; traces: {mode}\n")

    summaries = asyncio.run(run_experiments(args, truth, patient_ids))
    logfire.force_flush()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"experiment_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps({"dataset": DATASET_NAME, "as_of": settings.as_of.isoformat(),
                                "experiments": summaries}, indent=2, default=str))
    print(f"summary: {path}")
    return 0 if all(s["task_failures"] == 0 for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
