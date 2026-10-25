"""Reference labels from a model that reads the chart and the policy's wording, nothing else (G1.3).

    uv run --project api python evals/label.py --estimate                     # G1.4: tokens and cost, no call
    uv run --project api python evals/label.py --out /tmp/labels              # scripted run (fake batch client)
    uv run --project api python evals/label.py --live                         # the G1.5 run: needs approval
    uv run --project api python evals/label.py --export web/public/labels.json  # the review page's file

Run from the repo root so the one top-level .env is found.

What the labeler sees, per case, in one request covering all five criteria: the service line, each
criterion's id and `description` (no codes, keywords or evidence specs), the as-of date and the chart as
evals/chart.py renders it. Its instructions are in evals/labeler_prompt.md, whose sha256 is recorded with the
labels. Nothing is imported from app.graph or app.llm.prompts.

Before a request is built into a batch, the whole prompt goes through the pipeline's PHI guard
(app.llm.guard.assert_clean) against a vault holding the Patient resource's names, identifiers and id. A hit
stops that case: it is recorded as unlabeled and never sent.

Requests go through the Message Batches API. Each reply is checked: the schema, all five criteria, and every
cited record present in that chart. An `evidence` label must cite at least one record. An invalid reply is retried once in a follow-up batch, then recorded as
unlabeled. An errored, canceled or expired result, a refusal and a reply cut off at max_tokens are recorded
as unlabeled with the reason, without a retry.

Two runs (D19). Where they give the same status the label is that status, with the union of their records
and run 1's reason. Where they disagree, or either run has no label, the label is `unlabeled` and both runs
are kept under `runs`.

Without --live the same code runs against a scripted fake batch client, so nothing is sent. --live also
needs ANTHROPIC_API_KEY, writes evals/labels/held_out.yaml and evals/labels/hard.yaml, and refuses to
overwrite them (labels are not edited after the run).
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import yaml

from app.config import settings
from app.llm.guard import PhiLeak, assert_clean
from app.mcp.store import FhirStore
from app.phi.vault import Vault
from chart import chart_records, estimate_tokens, find_patient, format_chart, load_keys

ROOT = Path(__file__).resolve().parents[1]
EVALS = Path(__file__).resolve().parent
PROMPT_FILE = EVALS / "labeler_prompt.md"
CRITERIA_FILE = ROOT / "data" / "payer_criteria" / "adalimumab_ra.yaml"
CASES_FILE = ROOT / "data" / "hard" / "cases.yaml"
LABELS_DIR = EVALS / "labels"
EXPORT_FILE = ROOT / "web" / "public" / "labels.json"
SPLITS = ("held_out", "hard")

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
EFFORT = "medium"
AS_OF = "2026-09-20"
RUNS = 2
# Sonnet 5.5 list price is $2 / $10 per million input / output tokens; the Batches API is half.
BATCH_PRICE_PER_MTOK = {"input": 1.00, "output": 5.00}
# Output allowance per request for the estimate: five one-line reasons and their citations (~600 tokens)
# plus adaptive thinking at medium effort over a long chart, which is billed as output.
OUTPUT_ALLOWANCE = 4000
MARGIN = 0.25
EXCERPT_CHARS = 600
MAX_BATCH_BYTES = 200_000_000  # the API's limit is 256 MB per batch
POLL_SECONDS = 60
USER_MARKER = "=== user ===\n"
STATUSES = ("evidence", "gap")
REF = re.compile(r"\b(?:Condition|MedicationRequest|Observation|DocumentReference)/[A-Za-z0-9.\-]+")


# --- what the labeler is told -------------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    service: str
    criteria: tuple[tuple[str, str], ...]  # (id, description)

    @property
    def ids(self) -> list[str]:
        return [cid for cid, _ in self.criteria]


def load_policy(path: Path = CRITERIA_FILE) -> Policy:
    """Only `service` and each criterion's id and description: no codes, keywords or evidence specs."""
    raw = yaml.safe_load(path.read_text())
    return Policy(" ".join(raw["service"].split()),
                  tuple((c["id"], " ".join(c["description"].split())) for c in raw["criteria"]))


def prompt_sha256(path: Path = PROMPT_FILE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_prompt(policy: Policy, chart_text: str, path: Path = PROMPT_FILE) -> tuple[str, str]:
    """(system, user). The chart goes in last, so nothing in it is read as a placeholder."""
    system, user = path.read_text().split(USER_MARKER, 1)
    criteria = "\n".join(f"- {cid}: {description}" for cid, description in policy.criteria)
    user = (user.replace("{service}", policy.service).replace("{as_of}", AS_OF)
            .replace("{criteria}", criteria).replace("{chart}", chart_text))
    return system.strip() + "\n", user


def reply_schema(policy: Policy) -> dict[str, Any]:
    label = {"type": "object",
             "properties": {"status": {"type": "string", "enum": list(STATUSES)},
                            "records": {"type": "array", "items": {"type": "string"}},
                            "reason": {"type": "string"}},
             "required": ["status", "records", "reason"], "additionalProperties": False}
    return {"type": "object", "properties": {cid: label for cid in policy.ids},
            "required": policy.ids, "additionalProperties": False}


def request_params(policy: Policy, system: str, user: str) -> dict[str, Any]:
    # No temperature/top_p/top_k (400 on the current API), no forced tool_choice (400 on Sonnet 5.5), no
    # `fallbacks` (rejected on Batches). Thinking is left at Sonnet 5.5's default (adaptive).
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": EFFORT,
                              "format": {"type": "json_schema", "schema": reply_schema(policy)}}}


# --- the cases ----------------------------------------------------------------------------------------

@dataclass
class Case:
    split: str
    key: str
    refs: frozenset[str]
    system: str
    user: str
    blocked: str | None = None  # why the PHI guard stopped it

    @property
    def prompt_chars(self) -> int:
        return len(self.system) + len(self.user)


def hard_cases(path: Path = CASES_FILE) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text())["cases"]


def patient_resource(fhir_dir: Path, patient_id: str) -> dict[str, Any]:
    (path,) = fhir_dir.glob(f"*_{patient_id}.json")
    entries = json.loads(path.read_text())["entry"]
    return next(e["resource"] for e in entries if e["resource"]["resourceType"] == "Patient")


def patient_vault(patient: dict[str, Any]) -> Vault:
    """The values the guard must never see leave: every name (each token and in full), every identifier
    value and the patient id."""
    vault = Vault()
    for name in patient.get("name") or []:
        tokens = [*name.get("given", []), *([name["family"]] if name.get("family") else [])]
        for token in tokens:
            vault.placeholder("NAME", token)
        if tokens:
            vault.placeholder("NAME", " ".join(tokens))
    for identifier in patient.get("identifier") or []:
        if identifier.get("value"):
            vault.placeholder("ID", identifier["value"])
    vault.placeholder("PATIENT", patient["id"])
    return vault


def phi_check(patient: dict[str, Any], system: str, user: str) -> str | None:
    vault = patient_vault(patient)
    try:
        assert_clean(system, vault)
        assert_clean(user, vault)
    except PhiLeak as e:
        return str(e)
    finally:
        vault.clear()
    return None


def load_case(split: str, key: str, store: FhirStore, fhir_dir: Path, policy: Policy) -> Case:
    patient_id = find_patient(store, key)
    records = chart_records(store.chart(patient_id), key)
    system, user = build_prompt(policy, format_chart(records))
    refs = frozenset(r["ref"] for section in ("conditions", "medication_requests", "observations", "notes")
                     for r in records[section])
    blocked = phi_check(patient_resource(fhir_dir, patient_id), system, user)
    return Case(split, key, refs, system, user, blocked)


def split_keys(split: str) -> list[str]:
    return load_keys() if split == "held_out" else [c["key"] for c in hard_cases()]


def load_cases(dirs: dict[str, Path], policy: Policy) -> list[Case]:
    cases = []
    for split in SPLITS:
        store = FhirStore(dirs[split])
        cases += [load_case(split, key, store, dirs[split], policy) for key in split_keys(split)]
    return cases


# --- reading one reply --------------------------------------------------------------------------------

@dataclass
class Reply:
    kind: str  # "ok", "invalid" (retried once) or "failed" (unlabeled, no retry)
    labels: dict[str, dict[str, Any]] | None = None
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def validate(text: str, policy: Policy, refs: frozenset[str]) -> dict[str, dict[str, Any]]:
    """The reply's labels, or ValueError naming what is wrong."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    missing, extra = set(policy.ids) - set(data), set(data) - set(policy.ids)
    if missing or extra:
        raise ValueError(f"criteria missing {sorted(missing)} / unexpected {sorted(extra)}")
    labels = {}
    for cid in policy.ids:
        label = data[cid]
        if not isinstance(label, dict) or set(label) != {"status", "records", "reason"}:
            raise ValueError(f"{cid}: needs exactly status, records and reason")
        if label["status"] not in STATUSES:
            raise ValueError(f"{cid}: status {label['status']!r}")
        if not isinstance(label["records"], list) or not all(isinstance(r, str) for r in label["records"]):
            raise ValueError(f"{cid}: records is not a list of strings")
        if not isinstance(label["reason"], str) or not label["reason"].strip():
            raise ValueError(f"{cid}: no reason")
        if label["status"] == "evidence" and not label["records"]:
            # Enforced here, not in the schema: minItems would have to depend on status, and the
            # structured-output schema has no conditional (if/then) to say so.
            raise ValueError(f"{cid}: evidence with no records cited")
        unknown = [r for r in label["records"] if r not in refs]
        if unknown:
            raise ValueError(f"{cid}: cites records not in the chart: {unknown[:3]}")
        labels[cid] = {"status": label["status"], "records": list(dict.fromkeys(label["records"])),
                       "reason": _one_line(label["reason"])}
    return labels


def read_result(result: Any, policy: Policy, refs: frozenset[str]) -> Reply:
    if result is None:
        return Reply("failed", reason="no result returned for this request")
    kind = result.type
    if kind == "errored":
        error = getattr(result.error, "error", result.error)
        return Reply("failed", reason=f"request errored: {getattr(error, 'type', 'error')}: "
                                      f"{_one_line(getattr(error, 'message', ''))}")
    if kind in ("canceled", "expired"):
        return Reply("failed", reason=f"request {kind}")
    if kind != "succeeded":
        return Reply("failed", reason=f"unknown result type {kind!r}")
    message = result.message
    usage = getattr(message, "usage", None)
    tokens = {"input_tokens": getattr(usage, "input_tokens", 0) or 0,
              "output_tokens": getattr(usage, "output_tokens", 0) or 0}
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None)
        return Reply("failed", reason=f"the model refused (category {category or 'none given'})", **tokens)
    if message.stop_reason == "max_tokens":
        return Reply("failed", reason=f"reply cut off at max_tokens ({MAX_TOKENS})", **tokens)
    text = next((b.text for b in message.content if b.type == "text"), None)
    if text is None:
        return Reply("invalid", reason="no text block in the reply", **tokens)
    try:
        return Reply("ok", labels=validate(text, policy, refs), **tokens)
    except ValueError as e:
        return Reply("invalid", reason=str(e), **tokens)


# --- the batches --------------------------------------------------------------------------------------

def custom_id(case: Case, run: int, attempt: int) -> str:
    return f"{case.split.replace('_', '-')}-{case.key}-r{run}-a{attempt}"  # ^[a-zA-Z0-9_-]{1,64}$


def _chunks(requests: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    chunk, size = [], 0
    for request in requests:
        n = len(json.dumps(request))
        if chunk and size + n > MAX_BATCH_BYTES:
            yield chunk
            chunk, size = [], 0
        chunk.append(request)
        size += n
    if chunk:
        yield chunk


def submit(client: Any, requests: list[dict[str, Any]], poll_seconds: float,
           sleep: Callable[[float], None], log: Callable[[str], None]) -> dict[str, Any]:
    """Send the requests as one or more batches, wait for each to end, and return result by custom_id."""
    if not requests:
        return {}
    batches = [(client.messages.batches.create(requests=chunk).id, len(chunk)) for chunk in _chunks(requests)]
    results: dict[str, Any] = {}
    for batch_id, count in batches:
        log(f"batch {batch_id}: {count} requests submitted")
        while (status := client.messages.batches.retrieve(batch_id).processing_status) != "ended":
            log(f"batch {batch_id}: {status}")
            sleep(poll_seconds)
        for item in client.messages.batches.results(batch_id):  # any order: keyed by custom_id
            results[item.custom_id] = item.result
    return results


@dataclass
class RunOutcome:
    labels: dict[str, dict[str, Any]] | None = None
    failure: str = ""


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost(self) -> float:
        return (self.input_tokens * BATCH_PRICE_PER_MTOK["input"]
                + self.output_tokens * BATCH_PRICE_PER_MTOK["output"]) / 1_000_000


@dataclass
class Labeling:
    outcomes: dict[tuple[str, str], list[RunOutcome]] = field(default_factory=dict)
    usage: dict[str, Usage] = field(default_factory=lambda: {s: Usage() for s in SPLITS})


def run_labeling(client: Any, cases: list[Case], policy: Policy, runs: int = RUNS,
                 poll_seconds: float = POLL_SECONDS, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = lambda _: None) -> Labeling:
    labeling = Labeling()
    for case in cases:
        labeling.outcomes[(case.split, case.key)] = [RunOutcome() for _ in range(runs)]
    pending = [(case, run) for case in cases if case.blocked is None for run in range(1, runs + 1)]
    first_error: dict[tuple[str, str, int], str] = {}
    for attempt in (1, 2):
        by_id = {custom_id(case, run, attempt): (case, run) for case, run in pending}
        requests = [{"custom_id": cid, "params": request_params(policy, case.system, case.user)}
                    for cid, (case, run) in by_id.items()]
        results = submit(client, requests, poll_seconds, sleep, log)
        retry = []
        for cid, (case, run) in by_id.items():
            reply = read_result(results.get(cid), policy, case.refs)
            usage = labeling.usage[case.split]
            usage.requests += 1
            usage.input_tokens += reply.input_tokens
            usage.output_tokens += reply.output_tokens
            slot = labeling.outcomes[(case.split, case.key)][run - 1]
            if reply.kind == "ok":
                slot.labels = reply.labels
            elif reply.kind == "invalid" and attempt == 1:
                first_error[(case.split, case.key, run)] = reply.reason
                retry.append((case, run))
            elif reply.kind == "invalid":
                slot.failure = (f"invalid reply, twice (first: {first_error[(case.split, case.key, run)]}; "
                                f"retry: {reply.reason})")
            else:
                slot.failure = reply.reason
        pending = retry
    for case in cases:
        if case.blocked is not None:
            for slot in labeling.outcomes[(case.split, case.key)]:
                slot.failure = f"not sent: the PHI guard stopped it ({case.blocked})"
    return labeling


# --- combining the two runs ---------------------------------------------------------------------------

def _run_entry(outcome: RunOutcome, cid: str) -> dict[str, Any]:
    if outcome.labels is None:
        return {"status": "unlabeled", "records": [], "reason": outcome.failure}
    return dict(outcome.labels[cid])


def combine(outcomes: list[RunOutcome], policy: Policy) -> dict[str, dict[str, Any]]:
    first = outcomes[0]
    if all(o.labels is None and o.failure == first.failure for o in outcomes):
        # The case failed the same way in every run (the PHI guard, say): one reason says it all.
        return {cid: {"status": "unlabeled", "records": [], "reason": first.failure} for cid in policy.ids}
    out = {}
    for cid in policy.ids:
        entries = [_run_entry(o, cid) for o in outcomes]
        statuses = {e["status"] for e in entries}
        if len(statuses) == 1 and "unlabeled" not in statuses:
            records = list(dict.fromkeys(r for e in entries for r in e["records"]))
            out[cid] = {"status": entries[0]["status"], "records": records, "reason": entries[0]["reason"]}
            continue
        if "unlabeled" in statuses:
            failed = [str(i) for i, e in enumerate(entries, 1) if e["status"] == "unlabeled"]
            reason = f"run {' and '.join(failed)} gave no label"
        else:
            reason = "the two runs disagreed: " + ", ".join(
                f"run {i} {e['status']}" for i, e in enumerate(entries, 1))
        out[cid] = {"status": "unlabeled", "records": [], "reason": reason, "runs": entries}
    return out


# --- the label files ----------------------------------------------------------------------------------

def label_file(split: str, cases: list[Case], labeling: Labeling, policy: Policy, *, scripted: bool,
               today: str) -> dict[str, Any]:
    usage = labeling.usage[split]
    labels = {case.key: combine(labeling.outcomes[(case.split, case.key)], policy)
              for case in cases if case.split == split}
    counts = {s: 0 for s in (*STATUSES, "unlabeled")}
    for case_labels in labels.values():
        for label in case_labels.values():
            counts[label["status"]] += 1
    return {"labeler": MODEL, "prompt_sha256": prompt_sha256(), "date": today, "runs": RUNS,
            "scripted": scripted, "split": split, "as_of": AS_OF, "effort": EFFORT,
            "requests": usage.requests, "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens, "estimated_cost_usd": round(usage.cost, 2),
            "counts": counts, "cases": labels}


def write_labels(out_dir: Path, cases: list[Case], labeling: Labeling, policy: Policy, *, scripted: bool,
                 today: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for split in SPLITS:
        path = out_dir / f"{split}.yaml"
        body = label_file(split, cases, labeling, policy, scripted=scripted, today=today)
        path.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=120))
        paths.append(path)
    return paths


# --- the review page's export -------------------------------------------------------------------------

def excerpt(text: str | None, limit: int = EXCERPT_CHARS) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(None, 1)[0] if " " in text[:limit] else text[:limit]
    return cut.rstrip() + " …"


def _expected(spec: dict[str, Any] | None) -> str | None:
    # D20: where cases.yaml marks `targeted`, only targeted criteria carry an expected answer.
    if spec is None or ("targeted" in spec and not spec["targeted"]):
        return None
    return spec.get("expected")


def hard_expected(key: str, criterion: str, cases: list[dict[str, Any]] | None = None) -> str | None:
    spec = next((c.get("criteria", {}).get(criterion) for c in (cases or hard_cases()) if c["key"] == key), None)
    return _expected(spec)


def export(labels_dir: Path, dirs: dict[str, Path], policy: Policy) -> dict[str, Any]:
    files = {split: yaml.safe_load((labels_dir / f"{split}.yaml").read_text())
             for split in SPLITS if (labels_dir / f"{split}.yaml").exists()}
    if not files:
        raise SystemExit(f"no label files in {labels_dir}")
    first = next(iter(files.values()))
    answers = hard_cases()
    out_cases = []
    for split, body in files.items():
        store = FhirStore(dirs[split])
        for key, labels in body["cases"].items():
            records = chart_records(store.chart(find_patient(store, key)), key)
            by_ref = {r["ref"]: r for section in ("conditions", "medication_requests", "observations", "notes")
                      for r in records[section]}
            out_cases.append({"key": key, "split": split, "labels": [
                {"criterion": cid, "status": label["status"], "reason": label["reason"],
                 "expected": hard_expected(key, cid, answers) if split == "hard" else None,
                 "records": [{"ref": ref, "date": by_ref[ref]["date"], "summary": by_ref[ref]["summary"],
                              "text": excerpt(by_ref[ref]["text"])} for ref in label["records"]]}
                for cid, label in labels.items()]})
    return {"labeler": first["labeler"], "date": str(first["date"]),
            "scripted": any(bool(b.get("scripted")) for b in files.values()),
            "criteria": [{"id": cid, "description": d} for cid, d in policy.criteria],
            "cases": out_cases}


# --- the estimate (G1.4) ------------------------------------------------------------------------------

def estimate(cases: list[Case], runs: int = RUNS) -> str:
    price_in, price_out = BATCH_PRICE_PER_MTOK["input"], BATCH_PRICE_PER_MTOK["output"]
    lines = [f"G1.4 estimate for {MODEL}, batch prices ${price_in:.2f} input / ${price_out:.2f} output per "
             f"million tokens (half of list)",
             f"input: characters / 4 of the exact prompt (system + user), an estimate, not a count; "
             f"output allowance {OUTPUT_ALLOWANCE:,} tokens per request; {runs} runs; effort {EFFORT}", ""]
    total = 0.0
    for split in SPLITS:
        mine = [c for c in cases if c.split == split]
        sent = [c for c in mine if c.blocked is None]
        tokens = [estimate_tokens(c.system + c.user) for c in sent]
        input_tokens = sum(tokens) * runs
        output_tokens = len(sent) * runs * OUTPUT_ALLOWANCE
        cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
        total += cost
        lines += [f"{split}: {len(mine)} cases, {len(mine) - len(sent)} stopped by the PHI guard",
                  f"  input per run {sum(tokens):>12,}   largest prompt {max(tokens, default=0):,}",
                  f"  input x {runs}     {input_tokens:>12,}   ${input_tokens * price_in / 1e6:,.2f}",
                  f"  output x {runs}    {output_tokens:>12,}   ${output_tokens * price_out / 1e6:,.2f}",
                  f"  subtotal                       ${cost:,.2f}"]
        lines += [f"  stopped: {c.key} ({c.blocked})" for c in mine if c.blocked]
    lines += ["", f"total            ${total:,.2f}",
              f"with +{MARGIN:.0%} margin  ${total * (1 + MARGIN):,.2f}  "
              "(covers retries of invalid replies and thinking beyond the allowance)"]
    return "\n".join(lines)


# --- the scripted fake batch client -------------------------------------------------------------------

def succeeded(text: str, stop_reason: str = "end_turn", input_tokens: int = 0, output_tokens: int = 0,
              category: str | None = None) -> Any:
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason,
                              stop_details=SimpleNamespace(type="refusal", category=category)
                              if stop_reason == "refusal" else None,
                              usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens))
    return SimpleNamespace(type="succeeded", message=message)


def errored(error_type: str = "api_error", message: str = "") -> Any:
    return SimpleNamespace(type="errored",
                           error=SimpleNamespace(type="error",
                                                 error=SimpleNamespace(type=error_type, message=message)))


def ended(kind: str) -> Any:
    return SimpleNamespace(type=kind)


class ScriptedClient:
    """Stands in for anthropic.Anthropic() on the batch path. `reply(custom_id, params)` returns one
    result; results come back in reverse order, as the API may return them in any order."""

    def __init__(self, reply: Callable[[str, dict[str, Any]], Any], polls: int = 1):
        self._reply, self._polls = reply, polls
        self.batches: dict[str, list[dict[str, Any]]] = {}
        self._seen: dict[str, int] = {}
        self.messages = SimpleNamespace(batches=SimpleNamespace(
            create=self._create, retrieve=self._retrieve, results=self._results))

    def _create(self, requests: list[dict[str, Any]]) -> Any:
        batch_id = f"msgbatch_scripted_{len(self.batches) + 1}"
        self.batches[batch_id] = requests
        self._seen[batch_id] = 0
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def _retrieve(self, batch_id: str) -> Any:
        self._seen[batch_id] += 1
        done = self._seen[batch_id] > self._polls
        return SimpleNamespace(id=batch_id, processing_status="ended" if done else "in_progress")

    def _results(self, batch_id: str) -> Iterable[Any]:
        for request in reversed(self.batches[batch_id]):
            yield SimpleNamespace(custom_id=request["custom_id"],
                                  result=self._reply(request["custom_id"], request["params"]))


def scripted_reply(custom_id: str, params: dict[str, Any]) -> Any:
    """The CLI's scripted labeler: evidence for ra_diagnosis citing the chart's first rheumatoid arthritis
    condition, gap elsewhere. Wiring only; it reads nothing like the pipeline does."""
    user = params["messages"][0]["content"]
    ids = list(params["output_config"]["format"]["schema"]["properties"])
    ra = next((m.group(0) for line in user.splitlines() if "Condition/" in line and "heumatoid" in line
               for m in [REF.search(line)] if m), None)
    reply = {cid: {"status": "gap", "records": [], "reason": "Scripted reply: not shown in the chart."}
             for cid in ids}
    if ra and "ra_diagnosis" in reply:
        reply["ra_diagnosis"] = {"status": "evidence", "records": [ra],
                                 "reason": "Scripted reply: a rheumatoid arthritis condition is recorded."}
    return succeeded(json.dumps(reply), input_tokens=estimate_tokens(params["system"] + user), output_tokens=300)


# --- command line -------------------------------------------------------------------------------------

def default_dirs() -> dict[str, Path]:
    return {"held_out": settings.fhir_dir, "hard": settings.data_dir / "synthea" / "hard"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    dirs = default_dirs()
    parser.add_argument("--fhir-dir", type=Path, default=dirs["held_out"], help="the cohort's bundles")
    parser.add_argument("--hard-dir", type=Path, default=dirs["hard"], help="the hard split's bundles")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--estimate", action="store_true", help="G1.4: tokens and cost for both splits, no call")
    mode.add_argument("--export", type=Path, metavar="PATH",
                      help=f"write the review page's JSON (normally {EXPORT_FILE.relative_to(ROOT)})")
    parser.add_argument("--live", action="store_true", help="send the batches for real (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--out", type=Path, help=f"where label files go (default {LABELS_DIR.relative_to(ROOT)} "
                                                 "with --live; required for a scripted run)")
    parser.add_argument("--labels-dir", type=Path, default=LABELS_DIR, help="label files read by --export")
    args = parser.parse_args(argv)
    dirs = {"held_out": args.fhir_dir, "hard": args.hard_dir}
    policy = load_policy()

    if args.export:
        if args.live:
            parser.error("--export makes no call; drop --live")
        body = export(args.labels_dir, dirs, policy)
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {args.export}: {len(body['cases'])} cases")
        return 0

    cases = load_cases(dirs, policy)
    if args.estimate:
        if args.live:
            parser.error("--estimate makes no call; drop --live")
        print(estimate(cases))
        return 0

    if args.live:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or settings.anthropic_api_key
        if not api_key:
            raise SystemExit("--live needs ANTHROPIC_API_KEY")
        out_dir = args.out or LABELS_DIR
        existing = [p for p in (out_dir / f"{s}.yaml" for s in SPLITS) if p.exists()]
        if existing:
            raise SystemExit(f"refusing to overwrite {', '.join(map(str, existing))}: labels are not "
                             "edited after the run")
        import anthropic

        client, poll = anthropic.Anthropic(api_key=api_key), POLL_SECONDS
    else:
        if args.out is None:
            parser.error("a scripted run needs --out (evals/labels/ is for the live run's files)")
        out_dir, client, poll = args.out, ScriptedClient(scripted_reply), 0

    labeling = run_labeling(client, cases, policy, poll_seconds=poll,
                            log=lambda line: print(line, file=sys.stderr))
    for path in write_labels(out_dir, cases, labeling, policy, scripted=not args.live,
                             today=date.today().isoformat()):
        body = yaml.safe_load(path.read_text())
        print(f"wrote {path}: {len(body['cases'])} cases, {body['counts']}, "
              f"{body['input_tokens']:,} in / {body['output_tokens']:,} out, ${body['estimated_cost_usd']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
