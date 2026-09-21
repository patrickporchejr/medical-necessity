"""The eval dataset, run as LangSmith experiments (they appear under Datasets & Experiments).

    python evals/experiment.py                                    # both providers' configured models
    python evals/experiment.py --models anthropic:claude-haiku-4-5 gemini:gemini-3.8-flash --repeat 3
    python evals/experiment.py --patients loyd638 aaron697        # a subset, for a quick look

Run from the repo root so the one top-level .env is found. Each model is one experiment on the
same dataset; every case runs `--repeat` times, because a model's answer is not deterministic.
With no LANGSMITH_API_KEY the same evaluators run locally and nothing is uploaded.

What LangSmith stores is de-identified by construction: a case's input is only a short patient
key, its output is the scored result (the client drops everything not on an allowlist, see
app/observability.py), and the case name uses the same short key, never a patient's name.
"""

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from langsmith import Client, aevaluate, schemas

from app.config import settings
from app.llm.client import LLMResult, build_client
from app.observability import estimate_cost, flush, get_client, setup_observability, tracing
from dataset import GroundTruth, open_ground_truth
from run import RUNS_DIR, connect, run_case

DATASET_NAME = "prior-auth-adalimumab-ra"
CASES_PER_STRATUM = 2  # the smallest and the largest chart in each stratum
KEY_LENGTH = 8  # a case is known by the first 8 characters of its patient id, nothing longer
LOCAL_DATASET_ID = uuid.uuid5(uuid.NAMESPACE_URL, DATASET_NAME)


def patient_key(patient_id: str) -> str:
    return patient_id[:KEY_LENGTH]


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


def build_examples(
    truth: GroundTruth, patient_ids: list[str] | None = None, dataset_id: uuid.UUID = LOCAL_DATASET_ID
) -> list[schemas.Example]:
    """One example per patient. The expected labels come from ground truth, never typed by hand,
    so changing a rule such as how a completed order is treated relabels every case."""
    # Named patients always run, selected or not; the default is the stratified selection.
    chosen = ([(p, stratum_of(truth, p)) for p in patient_ids] if patient_ids is not None
              else select_patients(truth))
    keys = [patient_key(p) for p, _ in chosen]
    assert len(set(keys)) == len(keys), "two patients share a key; lengthen KEY_LENGTH"
    return [
        schemas.Example(
            id=uuid.uuid5(LOCAL_DATASET_ID, patient_key(patient_id)),
            dataset_id=dataset_id,
            inputs={"patient_key": patient_key(patient_id)},
            outputs={"kinds": {cid: "evidence" if truth.established(patient_id, cid) else "gap"
                               for cid in truth.criterion_ids}},  # criterion id -> "evidence" | "gap"
            metadata={"name": f"{stratum} · {patient_key(patient_id)}", "stratum": stratum,
                      "notes": len(truth.store.chart(patient_id).documents)},
        )
        for patient_id, stratum in chosen
    ]


def sync_dataset(client: Client, examples: list[schemas.Example]) -> list[schemas.Example]:
    """Make the LangSmith dataset hold exactly these examples' labels, and return them as the
    server knows them. Ids are stable per patient key, so a rerun updates rather than duplicates."""
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    else:
        dataset = client.create_dataset(
            DATASET_NAME, description="Prior-authorization packets scored against Synthea ground truth"
        )
    client.upsert_examples_multipart(upserts=[
        schemas.ExampleUpsertWithAttachments(
            id=e.id, dataset_id=dataset.id, inputs=e.inputs, outputs=e.outputs, metadata=e.metadata
        )
        for e in examples
    ])
    return list(client.list_examples(dataset_id=dataset.id, example_ids=[e.id for e in examples]))


# --- evaluators ---------------------------------------------------------------------------------
# A bool is a pass/fail score in LangSmith, a float a graded one. A run that failed to
# complete gets no scores at all, only `completed = False`, so failures cannot hide in averages.

def score_case(out: dict, expected: dict[str, str]) -> dict[str, bool | float]:
    """Every score for one case, from its result and the expected kind per criterion."""
    scores: dict[str, bool | float] = {"completed": out["ok"]}
    if out["ok"]:
        # The citation-resolution metrics, scored against ground truth by evals/metrics.
        for key in ("citation_resolution_rate", "citation_level_rate", "gap_accuracy"):
            if out[key] is not None:
                scores[key] = out[key]
        # Did the model make the right call on each criterion, and is every claim it made sound?
        chosen = {a["criterion_id"]: a["kind"] for a in out["assertions"]}
        scores["decisions_correct"] = chosen == expected
        scores["packet_fully_correct"] = (
            chosen == expected and all(a["truth_resolved"] for a in out["assertions"]) and not out["unaddressed"]
        )
        # Did the model write every id exactly as given? A repaired id still resolves, so this is
        # a reliability signal, not a truthfulness one.
        scores["ids_well_formed"] = out["citations_repaired"] == 0
    # The harness's own check: the runtime `verify` node and the offline ground truth must
    # agree on every assertion. A failure here is a bug in verify or extract, not the model.
    scores["verify_matches_truth"] = bool(out["ok"] and out["verify_agrees_with_truth"])
    return scores


def evaluate_case(run, example) -> dict:
    outputs = run.outputs or {}
    if "ok" not in outputs:  # the target raised
        return {"results": []}
    scores = score_case(outputs, example.outputs["kinds"])
    return {"results": [{"key": k, "score": v} for k, v in scores.items()]}


# --- running ------------------------------------------------------------------------------------

@dataclass
class Row:
    name: str
    key: str
    scores: dict[str, bool | float]
    output: dict  # the case result, de-identified
    error: str | None  # set only when the run itself raised


class _NullResponse:
    status_code, text, content, headers = 200, "{}", b"{}", {}

    def json(self):
        return {}

    def raise_for_status(self):
        pass


class _NullSession:
    """LangSmith's evaluate always records its runs through a client. With no key, give it one
    that goes nowhere, so a local run neither tries the network nor fails to authenticate."""

    headers: dict = {}

    def request(self, *args, **kwargs):
        return _NullResponse()

    def close(self):
        pass

    def mount(self, *args, **kwargs):
        pass


def local_client() -> Client:
    return Client(api_key="local", api_url="http://localhost:0", session=_NullSession(), auto_batch_tracing=False)


def make_target(session, llm, truth: GroundTruth):
    full_ids = {patient_key(c.patient_id): c.patient_id for c in truth.cases()}

    async def target(inputs: dict) -> dict:
        key = inputs["patient_key"]
        with tracing():
            result, _ = await run_case(session, llm, full_ids[key], truth, settings.as_of)
        return asdict(replace(result, patient_id=key))  # nothing longer than the key leaves

    return target


async def run_experiment(
    session, llm, truth: GroundTruth, examples: list[schemas.Example], *, name: str,
    repeat: int = 1, concurrency: int = 4, metadata: dict | None = None,
    client: Client | None = None, upload: bool = False,
) -> list[Row]:
    results = await aevaluate(
        make_target(session, llm, truth),
        data=examples,
        evaluators=[evaluate_case],
        experiment_prefix=name,
        metadata=metadata,
        num_repetitions=repeat,
        max_concurrency=concurrency,
        client=client or local_client(),
        upload_results=upload,
    )
    rows = []
    async for r in results:
        run, example = r["run"], r["example"]
        outputs = run.outputs if run.outputs and "ok" in run.outputs else {}
        rows.append(Row(
            name=example.metadata["name"],
            key=example.inputs["patient_key"],
            scores={e.key: e.score for e in r["evaluation_results"]["results"]},
            output=outputs,
            error=str(run.error) if run.error else None,
        ))
    return rows


def client_for(spec: str):
    provider, _, model = spec.partition(":")
    if provider not in ("anthropic", "gemini") or not model:
        raise SystemExit(f"--models entries look like provider:model, got {spec!r}")
    update = {"llm_provider": provider, ("anthropic_model" if provider == "anthropic" else "gemini_model"): model}
    return build_client(settings.model_copy(update=update)), provider, model


def averages(rows: list[Row]) -> dict[str, float]:
    keys = sorted({k for r in rows for k in r.scores})
    return {k: round(mean(float(r.scores[k]) for r in rows if k in r.scores), 3) for k in keys}


def summarize(spec: str, provider: str, model: str, rows: list[Row]) -> dict:
    outputs = [r.output for r in rows]
    tokens_in = sum(o.get("input_tokens") or 0 for o in outputs)
    tokens_out = sum(o.get("output_tokens") or 0 for o in outputs)
    cost = estimate_cost(LLMResult(parsed=None, provider=provider, model=model,
                                   input_tokens=tokens_in, output_tokens=tokens_out))
    return {
        "experiment": spec,
        "runs": len(rows),
        "task_failures": sum(r.error is not None for r in rows),
        "averages": averages(rows),
        "tokens": {"input": tokens_in, "output": tokens_out},
        "estimated_cost_usd": None if cost is None else round(cost, 4),
        "cases": [{"name": r.name, "scores": r.scores, "error": r.output.get("error") or r.error} for r in rows],
    }


def print_rows(spec: str, rows: list[Row]) -> None:
    print(f"{spec}")
    for r in sorted(rows, key=lambda r: r.name):
        failed = [k for k, v in r.scores.items() if v is False]
        print(f"  {r.name:<48}{'FAILED ' + r.error if r.error else ('ok' if not failed else 'not ' + ', '.join(failed))}")
    print("  averages: " + ", ".join(f"{k} {v}" for k, v in averages(rows).items()) + "\n")


async def run_experiments(args, truth: GroundTruth, patient_ids: list[str] | None) -> list[dict]:
    client = get_client()  # None without a LangSmith key: the run stays local
    examples = build_examples(truth, patient_ids)
    if client is not None:
        examples = sync_dataset(client, examples)
    summaries = []
    async with connect(args.mcp_url) as session:
        for spec in args.models:
            llm, provider, model = client_for(spec)
            rows = await run_experiment(
                session, llm, truth, examples, name=spec, repeat=args.repeat,
                concurrency=args.concurrency, client=client, upload=client is not None,
                metadata={"provider": provider, "model": model, "as_of": settings.as_of.isoformat(),
                          "repeat": args.repeat},
            )
            print_rows(spec, rows)
            summaries.append(summarize(spec, provider, model, rows))
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
    cases = build_examples(truth, patient_ids)
    print(f"dataset {DATASET_NAME}: {len(cases)} cases x {args.repeat} runs x {len(args.models)} models "
          f"= {len(cases) * args.repeat * len(args.models)} pipeline runs; as of {settings.as_of}; traces: {mode}\n")

    summaries = asyncio.run(run_experiments(args, truth, patient_ids))
    flush()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"experiment_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps({"dataset": DATASET_NAME, "as_of": settings.as_of.isoformat(),
                                "experiments": summaries}, indent=2, default=str))
    print(f"summary: {path}")
    return 0 if all(s["task_failures"] == 0 for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
