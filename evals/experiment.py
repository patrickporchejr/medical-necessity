"""The eval dataset, run as LangSmith experiments (they appear under Datasets & Experiments).

    python evals/experiment.py                                    # both providers' configured models
    python evals/experiment.py --models anthropic:claude-haiku-4-5-20251001 gemini:gemini-3.8-flash --repeat 3
    python evals/experiment.py --patients loyd638 aaron697        # a subset, for a quick look
    python evals/experiment.py --split held_out                   # the held-out split (or: hard, both, all)
    python evals/experiment.py --models scripted:faithful --split all --repeat 1   # no model call

The working split (the default) is what tuning reads. The held-out split is listed in
evals/held_out.txt (method at its top) and the hard split in data/hard/cases.yaml (built into
data/synthea/hard/ by data/hard/build.py); both run only when asked for. Each split is its own
experiment, LangSmith dataset and summary file, so splits are never averaged together.

What each split's decisions are scored against (G1.8):

- work: computed ground truth (dataset.GroundTruth.established), every criterion, as before.
- held_out: the reference labels in evals/labels/held_out.yaml (D19). An `unlabeled` label is left out of
  scoring and counted.
- hard: the answers in data/hard/cases.yaml on targeted criteria only (D20). An answer still `pending-CPG1`
  would be left out and counted (none is since D21); untargeted criteria are scored apart as a wiring check (`wiring:<criterion>`), never in the
  headline.

Each scored criterion is its own pass/fail score (`decision:<criterion>`), and summaries give counts beside
rates ("19 of 20"). The citation scores (citation_resolution_rate, citation_level_rate, gap_accuracy,
verify_matches_truth) are always against computed ground truth, on every split: they check the harness and
the citations, not the reference. `packet_fully_correct` mixes the two, so it is scored on the working split
only.

`--models scripted:faithful` runs the scripted model in evals/scripted.py (it follows the extract
node's summary exactly): no model is called, nothing is spent, and the harness runs end to end.

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
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import mean, median

import yaml
from langsmith import Client, aevaluate, schemas
from langsmith.utils import LangSmithNotFoundError

import app.llm.prompts
from app.config import settings
from app.llm.client import LLMResult, build_client
from app.observability import estimate_cost, flush, get_client, setup_observability, tracing
from dataset import CRITERIA_FILE, HARD_DIR, GroundTruth, open_ground_truth, pinned_as_of
from run import RUNS_DIR, connect, run_case

DATASET_NAME = "prior-auth-adalimumab-ra"
CASES_PER_STRATUM = 5  # per stratum of the working split, the smallest and largest chart among them
SPLITS = ("work", "held_out", "hard")
HELD_OUT_FILE = Path(__file__).parent / "held_out.txt"  # the only place the split is defined
LABELS_FILE = Path(__file__).parent / "labels" / "held_out.yaml"  # G1.5's reference labels, never edited
KEY_LENGTH = 8  # a case is known by the first 8 characters of its patient id, nothing longer
LOCAL_DATASET_ID = uuid.uuid5(uuid.NAMESPACE_URL, DATASET_NAME)
APP_DIR = Path(app.llm.prompts.__file__).parents[2]
REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = REPO_ROOT / "data" / "hard" / "cases.yaml"  # the hard split: its cases and our answers
PENDING = "pending-CPG1"
REFERENCES = {"work": "computed ground truth", "held_out": "reference labels (evals/labels/held_out.yaml)",
              "hard": "answers in data/hard/cases.yaml, targeted criteria only (D20)"}


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


def load_hard_cases(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or CASES_FILE).read_text())["cases"]


def hard_patients(truth: GroundTruth, cases: list[dict] | None = None) -> dict[str, dict]:
    """patient_id -> its hard case, for every case in cases.yaml. `truth` reads the hard split's bundles;
    a case missing from them means the split was not built (data/hard/build.py)."""
    cases = load_hard_cases() if cases is None else cases
    by_key = {patient_key(p): p for p in truth.store.patient_ids()}
    if missing := [c["key"] for c in cases if c["key"] not in by_key]:
        raise SystemExit(f"{len(missing)} hard case(s) not in {HARD_DIR} ({missing[:3]}); build the split with "
                         "data/hard/build.py")
    return {by_key[c["key"]]: c for c in cases}


def reference(split: str, key: str, criterion_ids: list[str], *, labels: dict | None = None,
              cases: list[dict] | None = None, kinds: dict[str, str] | None = None) -> dict:
    """What one case is scored against: `kinds` (criterion -> "evidence" | "gap", the scored ones),
    `excluded` (criterion -> why it is left out) and `wiring` (hard: untargeted criteria, scored apart).
    `kinds` is passed in for the working split (computed ground truth)."""
    if split == "work":
        return {"reference": REFERENCES[split], "kinds": kinds, "excluded": {}, "wiring": {}}
    scored, excluded, wiring = {}, {}, {}
    if split == "held_out":
        case = (labels or {}).get(key)
        if case is None:
            raise SystemExit(f"{LABELS_FILE.name} has no labels for held-out case {key}")
        for cid in criterion_ids:
            label = case[cid]
            if label["status"] in ("evidence", "gap"):
                scored[cid] = label["status"]
            else:
                runs = label.get("runs") or []
                both = runs and all(r["status"] in ("evidence", "gap") for r in runs)
                excluded[cid] = "unlabeled: runs disagreed" if both else "unlabeled: no valid reply"
    else:
        spec = next(c for c in cases if c["key"] == key)["criteria"]
        for cid in criterion_ids:
            answer = spec[cid]
            if not answer["targeted"]:
                wiring[cid] = answer["expected"]
            elif answer["expected"] == PENDING:
                excluded[cid] = PENDING
            else:
                scored[cid] = answer["expected"]
    return {"reference": REFERENCES[split], "kinds": scored, "excluded": excluded, "wiring": wiring}


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
    split: every held-out case (it is small and labeled in G1). Hard split: every case in
    data/hard/cases.yaml, in file order, its stratum `hard/<kind>`; `truth` must read the hard
    split's bundles. Only the split asked for is read, so tuning on the working split never
    touches a held-out chart."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
    if split == "hard":
        return [(pid, f"hard/{case['kind']}") for pid, case in hard_patients(truth).items()]
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
    return DATASET_NAME if split == "work" else f"{DATASET_NAME}-{split.replace('_', '-')}"


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
        if split == "hard":
            hard = hard_patients(truth)
            if wrong := [patient_key(p) for p in patient_ids if p not in hard]:
                raise SystemExit(f"case(s) {', '.join(wrong)} are not in the hard split")
            chosen = [(p, f"hard/{hard[p]['kind']}") for p in patient_ids]
        else:
            in_split = split_of(truth)
            if wrong := [patient_key(p) for p in patient_ids if in_split[p] != split]:
                raise SystemExit(f"case(s) {', '.join(wrong)} are not in the {split} split"
                                 + ("; pass --split held_out to run held-out cases" if split == "work" else ""))
            chosen = [(p, stratum_of(truth, p)) for p in patient_ids]
    else:
        chosen = select_patients(truth, split)
    keys = [patient_key(p) for p, _ in chosen]
    assert len(set(keys)) == len(keys), "two patients share a key; lengthen KEY_LENGTH"
    labels = yaml.safe_load(LABELS_FILE.read_text())["cases"] if split == "held_out" else None
    cases = load_hard_cases() if split == "hard" else None

    def expected(patient_id: str) -> dict:
        # criterion id -> "evidence" | "gap" under `kinds`, plus what is left out and why
        computed = ({cid: "evidence" if truth.established(patient_id, cid) else "gap"
                     for cid in truth.criterion_ids} if split == "work" else None)
        return reference(split, patient_key(patient_id), truth.criterion_ids, labels=labels, cases=cases,
                         kinds=computed)

    return [
        schemas.Example(
            id=example_id(patient_key(patient_id), split),
            dataset_id=dataset_id,
            inputs={"patient_key": patient_key(patient_id)},
            outputs=expected(patient_id),
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
    try:
        existing = {e.id for e in client.list_examples(dataset_id=dataset.id, example_ids=ids)}
    except LangSmithNotFoundError:
        # LangSmith answers 404 "Examples not found" when none of the ids exist yet (a new
        # dataset, or a new split's first sync), rather than an empty list.
        existing = set()
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

def score_case(out: dict, expected: dict[str, str], *, wiring: dict[str, str] | None = None,
               excluded: dict[str, str] | None = None, computed: bool = True) -> dict[str, bool | float]:
    """Every score for one case, from its result and the expected kind per scored criterion. The
    packet scores come twice: for the final packet, and with a `first_pass_` prefix for the model's
    first one, so the repair loop cannot hide model quality. They are equal when no repair ran.
    `excluded` criteria are not scored; `wiring` ones are scored apart (`wiring:<criterion>`).
    `computed` is false when `expected` is not computed ground truth (held-out, hard)."""
    scores: dict[str, bool | float] = {"completed": out["ok"]}
    if out["ok"]:
        known = set(expected) | set(wiring or {}) | set(excluded or {})
        scores |= packet_scores(out, expected, known, computed)
        scores |= {f"first_pass_{k}": v
                   for k, v in packet_scores(out["first_pass"], expected, known, computed).items()}
        chosen = {a["criterion_id"]: a["kind"] for a in out["assertions"]}
        scores |= {f"decision:{cid}": chosen.get(cid) == kind for cid, kind in expected.items()}
        scores |= {f"wiring:{cid}": chosen.get(cid) == kind for cid, kind in (wiring or {}).items()}
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


def packet_scores(packet: dict, expected: dict[str, str], known: set[str] | None = None,
                  computed: bool = True) -> dict[str, bool | float]:
    """One packet's scores: its assertions, unaddressed criteria and rates against ground truth.
    `known` is every criterion the case has (scored or not); a call on any other one is wrong."""
    scores: dict[str, bool | float] = {}
    # The citation-resolution metrics, scored against computed ground truth by evals/metrics.
    for key in ("citation_resolution_rate", "citation_level_rate", "gap_accuracy"):
        if packet[key] is not None:
            scores[key] = packet[key]
    # Did the model make the right call on each scored criterion, and is every claim it made sound?
    chosen = {a["criterion_id"]: a["kind"] for a in packet["assertions"]}
    known = set(expected) if known is None else known
    scores["decisions_correct"] = ({c: chosen.get(c) for c in expected} == expected
                                   and set(chosen) <= known)
    if computed:  # it checks the claims against computed ground truth, so it needs that as the reference
        scores["packet_fully_correct"] = (
            scores["decisions_correct"] and all(a["truth_resolved"] for a in packet["assertions"])
            and not packet["unaddressed"]
        )
    return scores


def evaluate_case(run, example) -> dict:
    outputs = run.outputs or {}
    if "ok" not in outputs:  # the target raised
        return {"results": []}
    ref = example.outputs
    scores = score_case(outputs, ref["kinds"], wiring=ref.get("wiring"), excluded=ref.get("excluded"),
                        computed=ref.get("reference", REFERENCES["work"]) == REFERENCES["work"])
    return {"results": [{"key": k, "score": v} for k, v in scores.items()]}


# --- running ------------------------------------------------------------------------------------

@dataclass
class Row:
    name: str
    key: str
    scores: dict[str, bool | float]
    output: dict  # the case result, de-identified
    error: str | None  # set only when the run itself raised
    expected: dict = field(default_factory=dict)  # the example's reference: kinds, excluded, wiring


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
            expected=dict(example.outputs),
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
        "reference": REFERENCES[split],
        # The file the decisions are scored against (none on the working split: computed ground truth).
        "reference_sha256": None if split == "work" else hashlib.sha256(
            (LABELS_FILE if split == "held_out" else CASES_FILE).read_bytes()).hexdigest(),
        "git_commit": git_commit(),
        "genai_prices": package_version("genai-prices"),
    }


def client_for(spec: str):
    if spec == "scripted:faithful":  # no model call: the scripted model the tests use
        from scripted import Scripted, faithful_from_prompt
        return Scripted(faithful_from_prompt), "scripted", "faithful"
    provider, _, model = spec.partition(":")
    if provider not in ("anthropic", "gemini") or not model:
        raise SystemExit(f"--models entries look like provider:model (or scripted:faithful), got {spec!r}")
    update = {"llm_provider": provider, ("anthropic_model" if provider == "anthropic" else "gemini_model"): model}
    return build_client(settings.model_copy(update=update)), provider, model


def averages(rows: list[Row]) -> dict[str, float]:
    keys = sorted({k for r in rows for k in r.scores})
    return {k: round(mean(float(r.scores[k]) for r in rows if k in r.scores), 3) for k in keys}


def of(n: int, total: int) -> str:
    return f"{n} of {total}"


def tallies(rows: list[Row]) -> dict[str, str]:
    """Every pass/fail score as a count beside its rate's denominator: "19 of 20"."""
    keys = sorted({k for r in rows for k, v in r.scores.items() if isinstance(v, bool)})
    return {k: of(sum(r.scores[k] is True for r in rows if k in r.scores), sum(k in r.scores for r in rows))
            for k in keys}


def scoring(rows: list[Row], split: str | None) -> dict:
    """What the decisions were scored against, and how much: criterion decisions over all completed runs
    (headline and per criterion), the wiring check apart, and the labels left out, counted once per case."""
    def count(prefix: str) -> dict:
        per: dict[str, list[bool]] = {}
        for r in rows:
            for k, v in r.scores.items():
                if k.startswith(prefix):
                    per.setdefault(k[len(prefix):], []).append(v)
        flat = [v for vs in per.values() for v in vs]
        return {"all": of(sum(flat), len(flat)), "rate": round(sum(flat) / len(flat), 3) if flat else None,
                "per_criterion": {c: of(sum(vs), len(vs)) for c, vs in sorted(per.items())}}

    cases = {r.key: r.expected for r in rows}
    excluded: dict[str, int] = {}
    for ref in cases.values():
        for why in (ref.get("excluded") or {}).values():
            excluded[why] = excluded.get(why, 0) + 1
    labels = lambda field_: sum(len(ref.get(field_) or {}) for ref in cases.values())
    return {
        "against": REFERENCES.get(split or "work"),
        "cases": len(cases),
        "completed_runs": of(sum(bool(r.scores.get("completed")) for r in rows), len(rows)),
        "criteria_scored": labels("kinds"),  # per case, not per run
        "criteria_excluded": dict(sorted(excluded.items())),  # why -> count, per case
        "decisions": count("decision:"),  # over completed runs
        **({"wiring": {"criteria": labels("wiring"), **count("wiring:")}} if labels("wiring") else {}),
        "citations_scored_against": "computed ground truth",
    }


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
        "tallies": tallies(rows),  # the pass/fail scores as counts: "19 of 20"
        "scoring": scoring(rows, (config or {}).get("split")),
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


def print_rows(spec: str, rows: list[Row], split: str | None = None) -> None:
    print(f"{spec}")
    for r in sorted(rows, key=lambda r: r.name):
        failed = [k for k, v in r.scores.items() if v is False]
        route = " [no model call]" if r.output.get("route") == "gap_packet" else ""
        print(f"  {r.name:<48}{'FAILED ' + r.error if r.error else ('ok' if not failed else 'not ' + ', '.join(failed))}{route}")
    counts = tallies(rows)
    print("  averages: " + ", ".join(f"{k} {v}" + (f" ({counts[k]})" if k in counts else "")
                                     for k, v in averages(rows).items()))
    s = scoring(rows, split)
    print(f"  decisions against {s['against']}: {s['decisions']['all']}"
          + (f"; left out: {', '.join(f'{w} {n}' for w, n in s['criteria_excluded'].items())}"
             if s["criteria_excluded"] else "")
          + (f"; wiring check (apart): {s['wiring']['all']}" if "wiring" in s else "") + "\n")


def open_split(split: str, mcp_url: str | None = None):
    """The tool session for one split. The hard split's bundles live in their own directory, so it gets
    its own in-process MCP server over them; a running server (the compose service) serves the cohort."""
    if split != "hard":
        return connect(mcp_url)
    if mcp_url:
        raise SystemExit("--mcp-url serves the cohort; the hard split runs on an in-process server")
    return _in_process(HARD_DIR)


@asynccontextmanager
async def _in_process(fhir_dir: Path):
    from mcp.shared.memory import create_connected_server_and_client_session
    from app.mcp.server import build_server

    server = build_server(fhir_dir, settings.criteria_dir)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        yield session


def truth_for(split: str, as_of: date) -> GroundTruth:
    """The rule over the split's bundles: the cohort, or the hard split's own directory."""
    return open_ground_truth(as_of, HARD_DIR if split == "hard" else None)


async def run_experiments(args, truth: GroundTruth, patient_ids: list[str] | None,
                          split: str = "work") -> list[dict]:
    client = get_client()  # None without a LangSmith key: the run stays local
    examples = build_examples(truth, patient_ids, split=split)
    if client is not None:
        examples = sync_dataset(client, examples, dataset_name(split))
    summaries = []
    async with open_split(split, args.mcp_url) as session:
        for spec in args.models:
            llm, provider, model = client_for(spec)
            config = run_config(provider, model, as_of=settings.as_of, repeat=args.repeat,
                                examples=examples, truth=truth, split=split)
            rows = await run_experiment(
                session, llm, truth, examples, name=f"{spec} {split}", repeat=args.repeat,
                concurrency=args.concurrency, client=client, upload=client is not None,
                metadata=config,
            )
            print_rows(f"{spec} [{split}]", rows, split)
            summaries.append(summarize(spec, provider, model, rows, config))
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--models", nargs="+",
                        default=[f"anthropic:{settings.anthropic_model}", f"gemini:{settings.gemini_model}"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--patients", nargs="+", help="name fragments or ids, all from --split; default is the split's selection")
    parser.add_argument("--split", choices=[*SPLITS, "both", "all"], default="work",
                        help="held-out and hard cases run only when asked for; both is work and held_out, all is "
                             "every split; each split gets its own summary file")
    parser.add_argument("--mcp-url")
    parser.add_argument("--out-dir", type=Path, default=RUNS_DIR)
    args = parser.parse_args(argv)

    mode = setup_observability(settings)
    splits = {"both": ["work", "held_out"], "all": list(SPLITS)}.get(args.split, [args.split])
    if set(splits) & {"held_out", "hard"}:
        # Scored against the labels and the hard answers, which were judged at one date: use it, and
        # refuse another. Every run in this call then uses it, the working split's too.
        settings.as_of_date = pinned_as_of(settings.as_of_date)
    truths = {split: truth_for(split, settings.as_of) for split in splits}
    patient_ids = None
    if args.patients:
        if len(splits) > 1:
            raise SystemExit("--patients names cases from one split; pick --split work, held_out or hard")
        from run import resolve_patient
        patient_ids = [resolve_patient(truths[splits[0]], p) for p in args.patients]
    for split in splits:
        cases = build_examples(truths[split], patient_ids, split=split)
        print(f"dataset {dataset_name(split)} ({split} split): {len(cases)} cases x {args.repeat} runs x "
              f"{len(args.models)} models = {len(cases) * args.repeat * len(args.models)} pipeline runs; "
              f"as of {settings.as_of}; traces: {mode}")
    print()

    # One summary file per split, so working-split results can be handed on without held-out ones.
    args.out_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for split in splits:
        summaries = asyncio.run(run_experiments(args, truths[split], patient_ids, split))
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
