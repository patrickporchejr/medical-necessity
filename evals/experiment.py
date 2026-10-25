"""The eval dataset, run as LangSmith experiments (they appear under Datasets & Experiments).

    python evals/experiment.py                                    # both providers' configured models
    python evals/experiment.py --models anthropic:claude-haiku-4-5-20251001 gemini:gemini-3.8-flash --repeat 3
    python evals/experiment.py --patients loyd638 aaron697        # a subset, for a quick look
    python evals/experiment.py --split held_out                   # the held-out split (or: both)

The working split (the default) is what tuning reads. The held-out split is listed in
evals/held_out.txt (method at its top) and runs only when asked for; each split is its own
experiment, LangSmith dataset and summary file, so the two are never averaged together.

Run from the repo root so the one top-level .env is found. Each model is one experiment on the
same dataset; every case runs `--repeat` times, because a model's answer is not deterministic.
With no LANGSMITH_API_KEY the same evaluators run locally and nothing is uploaded.

Each experiment's summary carries a `config` block: everything its result depends on (exact
model, as of date, repeat, the case keys, cohort, prompt and criteria hashes, git commit, the
genai-prices version). It holds no timestamp, so a rerun of the same inputs writes the same block.
To reproduce one: check out its commit, install from api/uv.lock (see api/pyproject.toml), set
AS_OF_DATE to its as_of, and run with its models and --repeat.

What LangSmith stores is de-identified by construction: a case's input is only a short patient
key, its output is the scored result (the client drops everything not on an allowlist, see
app/observability.py), and the case name uses the same short key, never a patient's name.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import mean, median

from langsmith import Client, aevaluate, schemas

import app.llm.prompts
from app.config import settings
from app.llm.client import LLMResult, build_client
from app.observability import estimate_cost, flush, get_client, setup_observability, tracing
from dataset import CRITERIA_FILE, GroundTruth, open_ground_truth
from run import RUNS_DIR, connect, run_case

DATASET_NAME = "prior-auth-adalimumab-ra"
CASES_PER_STRATUM = 5  # per stratum of the working split, the smallest and largest chart among them
SPLITS = ("work", "held_out")
HELD_OUT_FILE = Path(__file__).parent / "held_out.txt"  # the only place the split is defined
KEY_LENGTH = 8  # a case is known by the first 8 characters of its patient id, nothing longer
LOCAL_DATASET_ID = uuid.uuid5(uuid.NAMESPACE_URL, DATASET_NAME)
APP_DIR = Path(app.llm.prompts.__file__).parents[2]
REPO_ROOT = Path(__file__).resolve().parents[1]


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


def load_held_out(path: Path | None = None) -> list[str]:
    """The held-out case keys, in file order (sorted). Lines starting with # are the method."""
    path = path or HELD_OUT_FILE
    keys = [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    bad = [k for k in keys if len(k) != KEY_LENGTH]
    if bad or keys != sorted(set(keys)):
        raise ValueError(f"{path.name} must hold sorted, unique {KEY_LENGTH}-character keys (bad: {bad[:3]})")
    return keys


def split_of(truth: GroundTruth) -> dict[str, str]:
    """patient_id -> "work" or "held_out", for every case. Fails if the held-out file names a
    case the cohort lacks: the split is frozen, so a changed cohort needs a decision, not a guess."""
    held = set(load_held_out())
    ids = [c.patient_id for c in truth.cases()]
    if missing := held - {patient_key(p) for p in ids}:
        raise ValueError(f"{HELD_OUT_FILE.name} names {len(missing)} case(s) not in this cohort: "
                         f"{sorted(missing)[:3]}; the split was made for another cohort")
    return {p: "held_out" if patient_key(p) in held else "work" for p in ids}


def spread(ordered: list, k: int) -> list:
    """k items evenly spaced over a sorted list, both ends included; all of them if k >= len."""
    if len(ordered) <= k:
        return list(ordered)
    return [ordered[round(i * (len(ordered) - 1) / (k - 1))] for i in range(k)] if k > 1 else ordered[:1]


def select_patients(truth: GroundTruth, split: str = "work") -> list[tuple[str, str]]:
    """(patient_id, stratum) for one split. Working split: from each situation the rules
    distinguish, CASES_PER_STRATUM charts spread evenly by note count, the smallest and the
    largest among them, so the note-reading cap is exercised as well as the easy cases. Held-out
    split: every held-out case (it is small and hand-labeled in G1). Only the split asked for is
    read, so tuning on the working split never touches a held-out chart."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
    in_split = split_of(truth)
    groups: dict[str, list[tuple[int, str]]] = {}
    for pid, s in in_split.items():
        if s == split:
            groups.setdefault(stratum_of(truth, pid), []).append((len(truth.store.chart(pid).documents), pid))
    chosen = []
    for stratum in sorted(groups):
        ordered = sorted(groups[stratum])
        picks = ordered if split == "held_out" else spread(ordered, CASES_PER_STRATUM)
        chosen += [(pid, stratum) for _, pid in picks]
    return chosen


def dataset_name(split: str) -> str:
    """Held-out examples live in their own LangSmith dataset, so the working one never shows them."""
    return DATASET_NAME if split == "work" else f"{DATASET_NAME}-held-out"


def example_id(key: str, split: str = "work") -> uuid.UUID:
    namespace = LOCAL_DATASET_ID if split == "work" else uuid.uuid5(uuid.NAMESPACE_URL, dataset_name(split))
    return uuid.uuid5(namespace, key)


def build_examples(
    truth: GroundTruth, patient_ids: list[str] | None = None, dataset_id: uuid.UUID = LOCAL_DATASET_ID,
    split: str = "work",
) -> list[schemas.Example]:
    """One example per patient. The expected labels come from ground truth, never typed by hand,
    so changing a rule such as how a completed order is treated relabels every case."""
    # Named patients always run, selected or not, but only from the split asked for: a held-out
    # case cannot be run by name while tuning. The default is the split's selection.
    if patient_ids is not None:
        in_split = split_of(truth)
        if wrong := [patient_key(p) for p in patient_ids if in_split[p] != split]:
            raise SystemExit(f"case(s) {', '.join(wrong)} are not in the {split} split"
                             + ("; pass --split held_out to run held-out cases" if split == "work" else ""))
        chosen = [(p, stratum_of(truth, p)) for p in patient_ids]
    else:
        chosen = select_patients(truth, split)
    keys = [patient_key(p) for p, _ in chosen]
    assert len(set(keys)) == len(keys), "two patients share a key; lengthen KEY_LENGTH"
    return [
        schemas.Example(
            id=example_id(patient_key(patient_id), split),
            dataset_id=dataset_id,
            inputs={"patient_key": patient_key(patient_id)},
            outputs={"kinds": {cid: "evidence" if truth.established(patient_id, cid) else "gap"
                               for cid in truth.criterion_ids}},  # criterion id -> "evidence" | "gap"
            metadata={"name": f"{stratum} · {patient_key(patient_id)}", "stratum": stratum,
                      "split": split, "notes": len(truth.store.chart(patient_id).documents)},
        )
        for patient_id, stratum in chosen
    ]


def sync_dataset(client: Client, examples: list[schemas.Example],
                 name: str = DATASET_NAME) -> list[schemas.Example]:
    """Make the LangSmith dataset hold these examples with these labels (examples already there
    are updated, new ones created, others left alone), and return them as the server knows them.
    Ids are stable per patient key, so a rerun updates rather than duplicates."""
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(
            name, description="Prior-authorization packets scored against Synthea ground truth"
        )
    # `upsert_examples_multipart` rejects an id that already exists (409), so updates and
    # creates go through separate calls.
    ids = [e.id for e in examples]
    existing = {e.id for e in client.list_examples(dataset_id=dataset.id, example_ids=ids)}
    fields = lambda e: dict(id=e.id, inputs=e.inputs, outputs=e.outputs, metadata=e.metadata)
    if updated := [schemas.ExampleUpdate(**fields(e)) for e in examples if e.id in existing]:
        client.update_examples_multipart(dataset_id=dataset.id, updates=updated)
    if created := [schemas.ExampleUpsertWithAttachments(dataset_id=dataset.id, **fields(e))
                   for e in examples if e.id not in existing]:
        client.upsert_examples_multipart(upserts=created)
    return list(client.list_examples(dataset_id=dataset.id, example_ids=[e.id for e in examples]))


# --- evaluators ---------------------------------------------------------------------------------
# A bool is a pass/fail score in LangSmith, a float a graded one. A run that failed to
# complete gets no scores at all, only `completed = False`, so failures cannot hide in averages.

def score_case(out: dict, expected: dict[str, str]) -> dict[str, bool | float]:
    """Every score for one case, from its result and the expected kind per criterion. The packet
    scores come twice: for the final packet, and with a `first_pass_` prefix for the model's first
    one, so the repair loop cannot hide model quality. They are equal when no repair ran."""
    scores: dict[str, bool | float] = {"completed": out["ok"]}
    if out["ok"]:
        scores |= packet_scores(out, expected)
        scores |= {f"first_pass_{k}": v for k, v in packet_scores(out["first_pass"], expected).items()}
        # A float, so it is a rate in the averages and never reads as a pass or a fail. A repair
        # call that failed, leaving the first packet in place, does not count.
        scores["repaired"] = float(out["repairs"] > 0 and not out.get("repair_failed"))
        # Did the model write every id exactly as given? A repaired id still resolves, so this is
        # a reliability signal, not a truthfulness one.
        scores["ids_well_formed"] = out["citations_repaired"] == 0
        # Did every model call's first reply validate (the draft and any repair)? A retried one
        # completed, so this too is reliability.
        scores["schema_valid_first_try"] = out["schema_retries"] == 0
    # The harness's own check: the runtime `verify` node and the offline ground truth must
    # agree on every assertion. A failure here is a bug in verify or extract, not the model.
    scores["verify_matches_truth"] = bool(out["ok"] and out["verify_agrees_with_truth"])
    return scores


def packet_scores(packet: dict, expected: dict[str, str]) -> dict[str, bool | float]:
    """One packet's scores: its assertions, unaddressed criteria and rates against ground truth."""
    scores: dict[str, bool | float] = {}
    # The citation-resolution metrics, scored against ground truth by evals/metrics.
    for key in ("citation_resolution_rate", "citation_level_rate", "gap_accuracy"):
        if packet[key] is not None:
            scores[key] = packet[key]
    # Did the model make the right call on each criterion, and is every claim it made sound?
    chosen = {a["criterion_id"]: a["kind"] for a in packet["assertions"]}
    scores["decisions_correct"] = chosen == expected
    scores["packet_fully_correct"] = (
        chosen == expected and all(a["truth_resolved"] for a in packet["assertions"]) and not packet["unaddressed"]
    )
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


# --- the run config -----------------------------------------------------------------------------
# Everything a result depends on, written into each summary. Nothing here is a timestamp, so
# rerunning the same inputs reproduces the block exactly, and nothing identifies a patient: cases
# are short keys and the cohort is a hash of the synthetic patient ids.

def sha256_of(paths: list[Path], root: Path) -> str:
    """One hash over the files' names (relative to `root`) and contents, in a fixed order."""
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def prompt_files() -> list[Path]:
    """The prompt templates: the system prompts, and the assemble node that renders the user
    message from the evidence."""
    return [*(APP_DIR / "llm" / "prompts").glob("*.py"), APP_DIR / "graph" / "nodes" / "assemble.py"]


def cohort_fingerprint(patient_ids: list[str]) -> str:
    """sha256 of the sorted, newline-joined patient ids: the same value data/generate.sh prints."""
    return hashlib.sha256("".join(f"{p}\n" for p in sorted(patient_ids)).encode()).hexdigest()


def git_commit(root: Path = REPO_ROOT) -> str | None:
    """HEAD, with "-dirty" when tracked files differ from it. None outside a git checkout."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    try:
        sha = git("rev-parse", "HEAD")
        # Untracked files are not counted: eval output and local scratch would mark every run dirty.
        return sha + ("-dirty" if git("status", "--porcelain", "--untracked-files=no") else "")
    except (OSError, subprocess.CalledProcessError):
        return None


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def run_config(provider: str, model: str, *, as_of: date, repeat: int,
               examples: list[schemas.Example], truth: GroundTruth, split: str = "work") -> dict:
    patient_ids = truth.store.patient_ids()
    return {
        "provider": provider,
        "model": model,
        "as_of": as_of.isoformat(),
        "repeat": repeat,
        "split": split,
        "dataset": dataset_name(split),
        "case_keys": sorted(e.inputs["patient_key"] for e in examples),
        "cohort": {"patients": len(patient_ids), "sha256": cohort_fingerprint(patient_ids)},
        "prompts_sha256": sha256_of(prompt_files(), APP_DIR),
        "criteria_sha256": sha256_of([CRITERIA_FILE], CRITERIA_FILE.parent),
        "git_commit": git_commit(),
        "genai_prices": package_version("genai-prices"),
    }


def client_for(spec: str):
    provider, _, model = spec.partition(":")
    if provider not in ("anthropic", "gemini") or not model:
        raise SystemExit(f"--models entries look like provider:model, got {spec!r}")
    update = {"llm_provider": provider, ("anthropic_model" if provider == "anthropic" else "gemini_model"): model}
    return build_client(settings.model_copy(update=update)), provider, model


def averages(rows: list[Row]) -> dict[str, float]:
    keys = sorted({k for r in rows for k in r.scores})
    return {k: round(mean(float(r.scores[k]) for r in rows if k in r.scores), 3) for k in keys}


def latency(outputs: list[dict]) -> dict[str, dict[str, float | int]]:
    """Per graph node, the median and the slowest of its `seconds` over the runs that recorded it
    (a node a run never reached, such as assemble on the gap_packet route, is not counted as 0).
    A run that failed later still recorded the nodes it finished, so their laps count. A repaired
    run's assemble and verify are the sum of both passes, as the run recorded them."""
    laps: dict[str, list[float]] = {}
    for o in outputs:
        for node, seconds in (o.get("seconds") or {}).items():
            laps.setdefault(node, []).append(seconds)
    return {node: {"runs": len(xs), "p50": round(median(xs), 2), "max": round(max(xs), 2)}
            for node, xs in sorted(laps.items())}


def summarize(spec: str, provider: str, model: str, rows: list[Row], config: dict | None = None) -> dict:
    outputs = [r.output for r in rows]
    tokens_in = sum(o.get("input_tokens") or 0 for o in outputs)
    tokens_out = sum(o.get("output_tokens") or 0 for o in outputs)
    cost = estimate_cost(LLMResult(parsed=None, provider=provider, model=model,
                                   input_tokens=tokens_in, output_tokens=tokens_out))
    case_costs = [case_cost(provider, model, o) for o in outputs]
    by_route: dict[str, list[float | None]] = {}
    for o, c in zip(outputs, case_costs):
        if o.get("route"):
            by_route.setdefault(o["route"], []).append(c)
    return {
        "experiment": spec,
        "split": (config or {}).get("split"),  # "work" or "held_out"; never averaged together
        "config": config,
        "runs": len(rows),
        "task_failures": sum(r.error is not None for r in rows),
        "repaired_runs": sum(bool(o.get("repairs")) and not o.get("repair_failed") for o in outputs),
        "repair_failures": sum(bool(o.get("repair_failed")) for o in outputs),
        "schema_retries": sum(o.get("schema_retries") or 0 for o in outputs),
        "averages": averages(rows),
        "seconds": latency(outputs),  # per node: runs, p50, max
        "tokens": {"input": tokens_in, "output": tokens_out},
        "estimated_cost_usd": None if cost is None else round(cost, 4),
        "routes": {route: {"runs": len(costs),  # runs and cost per branch the graph took
                           "estimated_cost_usd": None if None in costs else round(sum(costs), 4)}
                   for route, costs in sorted(by_route.items())},
        "cases": [{"name": r.name, "route": r.output.get("route"), "scores": r.scores,
                   "estimated_cost_usd": None if c is None else round(c, 4),
                   "seconds": r.output.get("seconds") or {},
                   "error": r.output.get("error") or r.error} for r, c in zip(rows, case_costs)],
    }


def case_cost(provider: str, model: str, output: dict) -> float | None:
    """What one run spent. The gap_packet route calls no model, so it costs nothing whether or
    not the model has a known price."""
    if output.get("route") == "gap_packet":
        return 0.0
    return estimate_cost(LLMResult(parsed=None, provider=provider, model=model,
                                   input_tokens=output.get("input_tokens"),
                                   output_tokens=output.get("output_tokens")))


def print_rows(spec: str, rows: list[Row]) -> None:
    print(f"{spec}")
    for r in sorted(rows, key=lambda r: r.name):
        failed = [k for k, v in r.scores.items() if v is False]
        route = " [no model call]" if r.output.get("route") == "gap_packet" else ""
        print(f"  {r.name:<48}{'FAILED ' + r.error if r.error else ('ok' if not failed else 'not ' + ', '.join(failed))}{route}")
    print("  averages: " + ", ".join(f"{k} {v}" for k, v in averages(rows).items()) + "\n")


async def run_experiments(args, truth: GroundTruth, patient_ids: list[str] | None,
                          split: str = "work") -> list[dict]:
    client = get_client()  # None without a LangSmith key: the run stays local
    examples = build_examples(truth, patient_ids, split=split)
    if client is not None:
        examples = sync_dataset(client, examples, dataset_name(split))
    summaries = []
    async with connect(args.mcp_url) as session:
        for spec in args.models:
            llm, provider, model = client_for(spec)
            config = run_config(provider, model, as_of=settings.as_of, repeat=args.repeat,
                                examples=examples, truth=truth, split=split)
            rows = await run_experiment(
                session, llm, truth, examples, name=f"{spec} {split}", repeat=args.repeat,
                concurrency=args.concurrency, client=client, upload=client is not None,
                metadata=config,
            )
            print_rows(f"{spec} [{split}]", rows)
            summaries.append(summarize(spec, provider, model, rows, config))
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--models", nargs="+",
                        default=[f"anthropic:{settings.anthropic_model}", f"gemini:{settings.gemini_model}"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--patients", nargs="+", help="name fragments or ids, all from --split; default is the split's selection")
    parser.add_argument("--split", choices=[*SPLITS, "both"], default="work",
                        help="held-out cases run only when asked for; each split gets its own summary file")
    parser.add_argument("--mcp-url")
    parser.add_argument("--out-dir", type=Path, default=RUNS_DIR)
    args = parser.parse_args(argv)

    mode = setup_observability(settings)
    truth = open_ground_truth(settings.as_of)
    splits = list(SPLITS) if args.split == "both" else [args.split]
    patient_ids = None
    if args.patients:
        if len(splits) > 1:
            raise SystemExit("--patients names cases from one split; pick --split work or held_out")
        from run import resolve_patient
        patient_ids = [resolve_patient(truth, p) for p in args.patients]
    for split in splits:
        cases = build_examples(truth, patient_ids, split=split)
        print(f"dataset {dataset_name(split)} ({split} split): {len(cases)} cases x {args.repeat} runs x "
              f"{len(args.models)} models = {len(cases) * args.repeat * len(args.models)} pipeline runs; "
              f"as of {settings.as_of}; traces: {mode}")
    print()

    # One summary file per split, so working-split results can be handed on without held-out ones.
    args.out_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for split in splits:
        summaries = asyncio.run(run_experiments(args, truth, patient_ids, split))
        flush()
        written = datetime.now(timezone.utc)
        path = args.out_dir / f"experiment_{written:%Y%m%dT%H%M%SZ}_{split}.json"
        # The timestamp lives here, outside every experiment's config block.
        path.write_text(json.dumps({"dataset": dataset_name(split), "split": split,
                                    "as_of": settings.as_of.isoformat(),
                                    "written_at": written.isoformat(timespec="seconds"),
                                    "experiments": summaries}, indent=2, default=str))
        print(f"summary ({split}): {path}")
        failed |= any(s["task_failures"] for s in summaries)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
