"""The hard split: edited copies of cohort charts, each with answers we wrote (data/hard/cases.yaml).

    uv run --project api python data/hard/build.py                      # into data/synthea/hard/
    uv run --project api python data/hard/build.py --fhir-dir DIR --out DIR
    uv run --project api python data/hard/build.py --check [--fhir-dir DIR]

Run from the repo root. Each case in cases.yaml names a base chart (its 8-character key) in the cohort
(`--fhir-dir`, default data/synthea/fhir/), which is read and never written. The copy gets a new patient
id, uuid5(NAMESPACE_URL, "hard/" + name), and the records listed under the case's `edits`, appended at
the end of the bundle; nothing in the base chart is changed or removed. An added record's id is
uuid5(NAMESPACE_URL, "hard/<name>/<n>"), n its place in `edits`. Added notes are DocumentReferences
with base64 text, written the way Synthea writes them, so FhirStore reads the copies unchanged.

Nothing here is random: ids come from uuid5, dates and times from cases.yaml, and author, custodian
and requester are copied from the base chart's newest note. Two builds give the same bytes.

`--check` builds twice into temporary directories and verifies: the two builds are byte-identical,
every case key loads through FhirStore, every record named in `edits` and `records` exists, no base
is in evals/held_out.txt, every case answers every criterion, the sparse-situation cases sit in the
situation they name (by evals' stratum_of), and the cohort directory is unchanged (hashed before and
after). It prints one line per failure and exits 1 if there are any.
"""

import argparse
import base64
import hashlib
import json
import shutil
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CASES_FILE = HERE / "cases.yaml"
CRITERIA_FILE = REPO / "data" / "payer_criteria" / "adalimumab_ra.yaml"
HELD_OUT_FILE = REPO / "evals" / "held_out.txt"
DEFAULT_FHIR = REPO / "data" / "synthea" / "fhir"
DEFAULT_OUT = REPO / "data" / "synthea" / "hard"

AS_OF = date(2026, 9, 20)
KEY_LENGTH = 8
KINDS = ("negation", "conflict", "undetermined-date", "free-text-only", "screening", "injection",
         "sparse-situation")
EXPECTED = ("evidence", "gap", "pending-CPG1")
SPARSE_MIN = 5

METHOTREXATE = {"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "105585",
                "display": "Methotrexate 2.5 MG Oral Tablet"}
RA_CODE = "69896004"
# Screening tests the policy names, and the results a lab reports for them.
LAB_TESTS = {
    "71774-4": "Tuberculosis screening, interferon-gamma release assay (IGRA)",
    "5195-3": "Hepatitis B virus surface antigen (HBsAg)",
}
RESULTS = {"Negative": "260385009", "Positive": "10828004"}


def patient_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hard/" + name))


def record_id(name: str, n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"hard/{name}/{n}"))


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return yaml.safe_load(path.read_text())["cases"]


def held_out_keys(path: Path = HELD_OUT_FILE) -> set[str]:
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")}


def base_path(fhir_dir: Path, key: str) -> Path:
    found = [p for p in sorted(fhir_dir.glob(f"*_{key}*.json")) if p.stem.rsplit("_", 1)[-1].startswith(key)]
    if len(found) != 1:
        raise SystemExit(f"base {key}: expected one bundle in {fhir_dir}, found {len(found)}")
    return found[0]


# --- the added records -------------------------------------------------------------------------

def _stamp(day: str, time: str = "09:00:00") -> str:
    return f"{day}T{time}+00:00"


def _resources(bundle: dict, kind: str) -> list[dict]:
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == kind]


def _newest_note(bundle: dict) -> dict:
    return max(_resources(bundle, "DocumentReference"), key=lambda r: (r.get("date") or "", r["id"]))


def _ra_condition(bundle: dict) -> dict | None:
    conditions = [r for r in _resources(bundle, "Condition")
                  if any(c.get("code") == RA_CODE for c in r["code"].get("coding", []))]
    return max(conditions, key=lambda r: (r.get("onsetDateTime") or "", r["id"])) if conditions else None


def note(rid: str, pid: str, edit: dict, base: dict) -> dict:
    newest = _newest_note(base)
    text = f"\n{edit['date']}\n\n{edit['text']}"
    return {
        "resourceType": "DocumentReference",
        "id": rid,
        "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-documentreference"]},
        "status": "current",
        "type": {"coding": [
            {"system": "http://loinc.org", "code": "34117-2", "display": "History and physical note"},
            {"system": "http://loinc.org", "code": "51847-2", "display": "Evaluation + Plan note"},
        ]},
        "category": [{"coding": [{
            "system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
            "code": "clinical-note", "display": "Clinical Note"}]}],
        "subject": {"reference": f"urn:uuid:{pid}"},
        "date": _stamp(edit["date"], "09:00:00.000"),
        "author": newest.get("author", []),
        "custodian": newest.get("custodian", {}),
        "content": [{"attachment": {"contentType": "text/plain; charset=utf-8",
                                    "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}}],
        "context": {"period": {"start": _stamp(edit["date"]), "end": _stamp(edit["date"], "09:30:00")}},
    }


def medication_request(rid: str, pid: str, edit: dict, base: dict) -> dict:
    resource = {
        "resourceType": "MedicationRequest",
        "id": rid,
        "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-medicationrequest"]},
        "status": edit["status"],
        "intent": "order",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/medicationrequest-category",
            "code": "community", "display": "Community"}], "text": "Community"}],
        "medicationCodeableConcept": {"coding": [METHOTREXATE], "text": METHOTREXATE["display"]},
        "subject": {"reference": f"urn:uuid:{pid}"},
        "authoredOn": _stamp(edit["authored_on"]),
    }
    author = _newest_note(base).get("author") or []
    if author:
        resource["requester"] = author[0]
    if (ra := _ra_condition(base)) is not None:
        resource["reasonReference"] = [{"reference": f"urn:uuid:{ra['id']}",
                                        "display": ra["code"].get("text", "Rheumatoid arthritis (disorder)")}]
    return resource


def observation(rid: str, pid: str, edit: dict, base: dict) -> dict:
    display = LAB_TESTS[edit["code"]]
    value = edit["value"]
    return {
        "resourceType": "Observation",
        "id": rid,
        "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-observation-lab"]},
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory", "display": "Laboratory"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": edit["code"], "display": display}],
                 "text": display},
        "subject": {"reference": f"urn:uuid:{pid}"},
        "effectiveDateTime": _stamp(edit["date"]),
        "issued": _stamp(edit["date"], "15:00:00.000"),
        "valueCodeableConcept": {"coding": [{"system": "http://snomed.info/sct", "code": RESULTS[value],
                                             "display": value}], "text": value},
    }


BUILDERS = {"DocumentReference": note, "MedicationRequest": medication_request, "Observation": observation}


# --- building ----------------------------------------------------------------------------------

def build_case(case: dict, fhir_dir: Path) -> tuple[str, str]:
    """(file name, bundle text) for one case."""
    source = base_path(fhir_dir, case["base"])
    old_pid = source.stem.rsplit("_", 1)[-1]
    new_pid = patient_id(case["name"])
    # The patient id appears only as the Patient's id, its identifier and the urn:uuid references
    # to it; record ids share its first segments but never the whole id, so this swaps only those.
    bundle = json.loads(source.read_text().replace(old_pid, new_pid))
    for n, edit in enumerate(case.get("edits", [])):
        rid = record_id(case["name"], n)
        resource = BUILDERS[edit["resource"]](rid, new_pid, edit, bundle)
        bundle["entry"].append({"fullUrl": f"urn:uuid:{rid}", "resource": resource,
                                "request": {"method": "POST", "url": edit["resource"]}})
    name = f"{source.stem.rsplit('_', 1)[0]}_{new_pid}.json"
    return name, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"


def build(fhir_dir: Path, out: Path, cases: list[dict] | None = None) -> list[Path]:
    fhir_dir, out = fhir_dir.resolve(), out.resolve()
    if out == fhir_dir or fhir_dir in out.parents:
        raise SystemExit(f"--out {out} is inside the cohort {fhir_dir}; the cohort is never written")
    cases = cases if cases is not None else load_cases()
    built = [build_case(case, fhir_dir) for case in cases]
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.json"):  # the directory holds only this split
        stale.unlink()
    paths = []
    for name, text in built:
        path = out / name
        path.write_text(text)
        paths.append(path)
    return paths


# --- checking ----------------------------------------------------------------------------------

def tree_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(directory)).encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def check_spec(cases: list[dict]) -> list[str]:
    """What cases.yaml must hold on its own, without the cohort."""
    failures = []
    criteria = [c["id"] for c in yaml.safe_load(CRITERIA_FILE.read_text())["criteria"]]
    held = held_out_keys()
    names = [c["name"] for c in cases]
    if len(set(names)) != len(names):
        failures.append("case names are not unique")
    for case in cases:
        name = case["name"]
        if case["kind"] not in KINDS:
            failures.append(f"{name}: unknown kind {case['kind']!r}")
        if case["key"] != patient_id(name)[:KEY_LENGTH]:
            failures.append(f"{name}: key {case['key']} is not uuid5('hard/{name}')[:8]")
        if case["key"] == case["base"]:
            failures.append(f"{name}: key equals its base")
        if case["base"] in held:
            failures.append(f"{name}: base {case['base']} is in {HELD_OUT_FILE.name}")
        for n, edit in enumerate(case.get("edits", [])):
            if edit["id"] != record_id(name, n):
                failures.append(f"{name}: edit {n} id is not uuid5('hard/{name}/{n}')")
            day = edit.get("date") or edit.get("authored_on")
            if not isinstance(day, str) or date.fromisoformat(day) > AS_OF:
                failures.append(f"{name}: edit {n} date {day!r} is not a quoted date on or before {AS_OF}")
        if sorted(case["criteria"]) != sorted(criteria):
            failures.append(f"{name}: criteria {sorted(case['criteria'])} are not {sorted(criteria)}")
        for cid, answer in case["criteria"].items():
            if answer["expected"] not in EXPECTED:
                failures.append(f"{name}/{cid}: expected {answer['expected']!r} not in {EXPECTED}")
            reason = answer.get("reason") or ""
            if not reason.strip() or "\n" in reason.strip():
                failures.append(f"{name}/{cid}: reason must be one line")
            if not isinstance(answer.get("records"), list):
                failures.append(f"{name}/{cid}: records must be a list")
        if case["kind"] == "sparse-situation" and not case.get("situation"):
            failures.append(f"{name}: a sparse-situation case names its situation")
    missing = set(KINDS) - {c["kind"] for c in cases}
    if missing:
        failures.append(f"no case of kind {sorted(missing)}")
    situations = [c["situation"] for c in cases if c["kind"] == "sparse-situation"]
    for situation in sorted(set(situations)):
        if situations.count(situation) < SPARSE_MIN:
            failures.append(f"{situation}: {situations.count(situation)} cases, fewer than {SPARSE_MIN}")
    return failures


def check_built(cases: list[dict], out: Path) -> list[str]:
    """What the built split must hold: it loads, and every named record is in it."""
    sys.path.insert(0, str(REPO / "evals"))
    from app.graph.criteria import load_criteria
    from app.mcp.store import FhirStore, NotFound
    from dataset import GroundTruth
    from experiment import stratum_of

    failures = []
    store = FhirStore(out)
    truth = GroundTruth(store, load_criteria(CRITERIA_FILE), AS_OF)
    by_key = {pid[:KEY_LENGTH]: pid for pid in store.patient_ids()}
    if sorted(by_key) != sorted(c["key"] for c in cases):
        failures.append(f"built keys {sorted(by_key)} differ from cases.yaml")
    for case in cases:
        name, pid = case["name"], by_key.get(case["key"])
        if pid != patient_id(name):
            failures.append(f"{name}: no bundle with patient id {patient_id(name)}")
            continue
        chart = store.chart(pid)
        refs = [f"{e['resource']}/{e['id']}" for e in case.get("edits", [])]
        refs += [r for answer in case["criteria"].values() for r in answer["records"]]
        for ref in refs:
            kind, rid = ref.split("/", 1)
            try:
                chart.find(kind, rid)
            except NotFound:
                failures.append(f"{name}: {ref} is not in the built chart")
                continue
            if kind == "DocumentReference" and rid not in chart.document_text:
                failures.append(f"{name}: {ref} has no text")
        if case["kind"] == "sparse-situation" and (got := stratum_of(truth, pid)) != case["situation"]:
            failures.append(f"{name}: built chart is in {got}, not {case['situation']}")
    return failures


def check(fhir_dir: Path) -> list[str]:
    cases = load_cases()
    failures = check_spec(cases)
    cohort_keys = {p.stem.rsplit("_", 1)[-1][:KEY_LENGTH] for p in fhir_dir.glob("*.json")}
    failures += [f"{c['name']}: key {c['key']} is also a cohort key" for c in cases if c["key"] in cohort_keys]
    before = tree_hash(fhir_dir)
    with tempfile.TemporaryDirectory(prefix="hard-") as tmp:
        first, second = Path(tmp) / "a", Path(tmp) / "b"
        build(fhir_dir, first, cases)
        build(fhir_dir, second, cases)
        a = {p.name: p.read_bytes() for p in first.glob("*.json")}
        b = {p.name: p.read_bytes() for p in second.glob("*.json")}
        if a != b:
            failures.append(f"two builds differ: {sorted(n for n in a.keys() | b.keys() if a.get(n) != b.get(n))}")
        failures += check_built(cases, first)
    if tree_hash(fhir_dir) != before:
        failures.append(f"{fhir_dir} changed during the build")
    return failures


def digest(paths: list[Path]) -> str:
    """One checksum over the built split, for the handoff."""
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--fhir-dir", type=Path, default=DEFAULT_FHIR,
                        help=f"the cohort's patient bundles, read only (default {DEFAULT_FHIR})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"where the split goes (default {DEFAULT_OUT})")
    parser.add_argument("--check", action="store_true", help="build twice into a temp dir and verify; write nothing")
    args = parser.parse_args(argv)
    if not any(args.fhir_dir.glob("*.json")):
        raise SystemExit(f"no patient bundles (*.json) in {args.fhir_dir}")
    if args.check:
        failures = check(args.fhir_dir)
        for failure in failures:
            print(f"FAIL {failure}")
        print(f"{len(load_cases())} cases, {len(failures)} failures")
        return 1 if failures else 0
    paths = build(args.fhir_dir, args.out)
    print(f"{len(paths)} charts in {args.out.resolve()}, sha256 {digest(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
