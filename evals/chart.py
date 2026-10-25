"""One case's chart, printed whole, for a human reader or a model labeler (G1.1).

    uv run --project api python evals/chart.py 029f78a6                     # the chart, as text
    uv run --project api python evals/chart.py 029f78a6 --json              # the same records, as JSON
    uv run --project api python evals/chart.py 1a2b3c4d --fhir-dir data/synthea/hard
    uv run --project api python evals/chart.py --sizes                      # estimated tokens per held-out chart

Run from the repo root so the one top-level .env is found. A key is a case's 8-character key (the first
8 characters of its patient id, as in evals/held_out.txt).

What is shown: every condition, medication request, observation and note the store reads from the bundle,
each section sorted oldest first (then by ref), every note in full. Nothing is filtered by keyword or code,
and nothing comes from packets, ground truth or the pipeline (no import from app.graph or app.llm).

What is left out: patient demographics. There is no name, birth date, gender, address, identifier or full
patient id in the output; the case is named by its key only. Synthea writes the patient's name into every
note ("Jane1 Doe2 is a 52 year-old ..."), so in note text the patient's full name, and each of its tokens
on its own, reads `[patient]`. That is the only change made to a note. Note authors are left out too.

Record shape (`--json`, and what G1.3 and the review export read). The top level is

    {"key": "029f78a6", "conditions": [...], "medication_requests": [...], "observations": [...],
     "notes": [...]}

and every record in every list has the same six fields:

    ref      "Type/id", the citation (e.g. "Condition/029f78a6-...")
    date     YYYY-MM-DD as written in the chart (onset, authored, effective or note date), or null
    status   clinical status (conditions) or status (the rest), or null
    summary  one line: what the record is (display text, value and unit, dosage, reason, note type)
    code     "SYSTEM code" (e.g. "SNOMED 69896004"), or null for notes
    text     the note's full text (name tokens as above); null for every other record

The text view prints the same records, one line each, with each note's text in full beneath its line.

`--sizes` estimates input size for G1.4: per held-out key, the text view's characters / 4, labeled as an
estimate (no token-counting call is made), and the total.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.mcp.store import Chart, FhirStore, NotFound

KEY_LENGTH = 8
HELD_OUT_FILE = Path(__file__).parent / "held_out.txt"
SECTIONS = ("conditions", "medication_requests", "observations", "notes")
TITLES = {"conditions": "Conditions", "medication_requests": "Medication requests",
          "observations": "Observations", "notes": "Notes"}
PATIENT = "[patient]"
MIN_NAME_TOKEN = 3  # the PHI guard's floor (app.llm.guard.MIN_LENGTH); shorter tokens are not names
CHARS_PER_TOKEN = 4
SYSTEMS = {"http://snomed.info/sct": "SNOMED", "http://loinc.org": "LOINC",
           "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm"}


class UnknownKey(LookupError):
    pass


def find_patient(store: FhirStore, key: str) -> str:
    """The one patient id whose first 8 characters are `key`."""
    if len(key) != KEY_LENGTH:
        raise UnknownKey(f"a case key is {KEY_LENGTH} characters; got {key!r}")
    matches = [p for p in store.patient_ids() if p[:KEY_LENGTH] == key]
    if not matches:
        raise UnknownKey(f"no case with key {key} in this bundle directory")
    if len(matches) > 1:
        raise UnknownKey(f"key {key} names {len(matches)} cases in this bundle directory")
    return matches[0]


def _redactor(name: str):
    # The full name first, so it becomes one [patient], then any token of it standing alone.
    tokens = [t for t in name.split() if len(t) >= MIN_NAME_TOKEN]
    parts = sorted({name.strip(), *tokens} - {""}, key=len, reverse=True)
    if not parts:
        return lambda text: text
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, parts)) + r")(?!\w)", re.IGNORECASE)
    return lambda text: pattern.sub(PATIENT, text)


def _day(value: str | None) -> str | None:
    return value[:10] if value else None


def _code(code) -> str | None:
    if code is None:
        return None
    return f"{SYSTEMS.get(code.system, code.system or 'code')} {code.code}"


def _join(*parts: Any) -> str:
    return "; ".join(str(p) for p in parts if p not in (None, ""))


def _record(ref: str, date: str | None, status: str | None, summary: str, code: str | None,
            text: str | None = None) -> dict[str, Any]:
    return {"ref": ref, "date": _day(date), "status": status, "summary": summary, "code": code, "text": text}


def chart_records(chart: Chart, key: str) -> dict[str, Any]:
    """The chart as plain records (shape in the module docstring), each section sorted by date."""
    redact = _redactor(chart.patient.name)
    sections: dict[str, list[dict[str, Any]]] = {
        "conditions": [
            _record(f"Condition/{c.id}", c.onset_date, c.clinical_status,
                    _join(c.code.display or c.code.code,
                          c.verification_status and f"verification {c.verification_status}"),
                    _code(c.code))
            for c in chart.conditions
        ],
        "medication_requests": [
            _record(f"MedicationRequest/{m.id}", m.authored_on, m.status,
                    _join(m.code.display or m.code.code, m.dosage and f"dosage: {m.dosage}",
                          m.reason and f"reason: {m.reason}"),
                    _code(m.code))
            for m in chart.medication_requests
        ],
        "observations": [
            _record(f"Observation/{o.id}", o.effective_date, o.status,
                    f"{o.code.display or o.code.code}: "
                    + (" ".join(str(v) for v in (o.value, o.unit) if v is not None) or "no value"),
                    _code(o.code))
            for o in chart.observations
        ],
        "notes": [
            _record(f"DocumentReference/{d.id}", d.date, d.status,
                    (d.type.display or d.type.code) if d.type else "note", None,
                    redact(chart.document_text[d.id]))
            for d in chart.documents
        ],
    }
    for records in sections.values():
        records.sort(key=lambda r: (r["date"] or "", r["ref"]))
    return {"key": key, **sections}


def format_chart(records: dict[str, Any]) -> str:
    counts = ", ".join(f"{len(records[s])} {TITLES[s].lower()}" for s in SECTIONS)
    lines = [f"chart {records['key']}: {counts}",
             f"Dates as written in the chart. No demographics; the patient's name in notes reads {PATIENT}."]
    for section in SECTIONS:
        lines += ["", f"== {TITLES[section]} ({len(records[section])}) =="]
        for r in records[section]:
            head = "  ".join(x for x in (r["date"] or "no date", r["ref"], r["status"] or "no status",
                                         r["summary"], r["code"] and f"[{r['code']}]") if x)
            if r["text"] is None:
                lines.append(head)
            else:
                lines += ["", f"--- {head}", r["text"].strip("\n"), f"--- end {r['ref']}"]
    return "\n".join(lines) + "\n"


def load_keys(path: Path = HELD_OUT_FILE) -> list[str]:
    # Same reading as experiment.load_held_out, which this does not import (it pulls in app.graph).
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


def sizes(store: FhirStore, keys: list[str]) -> list[tuple[str, int]]:
    out = []
    for key in keys:
        text = format_chart(chart_records(store.chart(find_patient(store, key)), key))
        out.append((key, estimate_tokens(text)))
    return out


def format_sizes(rows: list[tuple[str, int]], source: Path) -> str:
    lines = [f"estimated tokens (characters / {CHARS_PER_TOKEN} of the text view; an estimate, not a count)",
             f"keys from {source.name}", ""]
    lines += [f"  {key}  {n:>9,}" for key, n in rows]
    lines += [f"  {'total':<8}  {sum(n for _, n in rows):>9,}  ({len(rows)} charts)"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("key", nargs="?", help=f"the case's {KEY_LENGTH}-character key")
    parser.add_argument("--fhir-dir", type=Path, default=settings.fhir_dir,
                        help=f"a directory of Synthea patient bundles (default {settings.fhir_dir})")
    parser.add_argument("--json", action="store_true", help="print the records as JSON")
    parser.add_argument("--sizes", action="store_true",
                        help=f"estimated tokens per chart for every key in {HELD_OUT_FILE.name}, and the total")
    args = parser.parse_args(argv)
    if args.sizes == bool(args.key):
        parser.error("give a case key, or --sizes (not both)")
    if not any(args.fhir_dir.glob("*.json")):
        raise SystemExit(f"no patient bundles (*.json) in {args.fhir_dir}")
    store = FhirStore(args.fhir_dir)
    try:
        if args.sizes:
            print(format_sizes(sizes(store, load_keys(HELD_OUT_FILE)), HELD_OUT_FILE))
            return 0
        records = chart_records(store.chart(find_patient(store, args.key)), args.key)
    except (UnknownKey, NotFound) as e:
        raise SystemExit(f"{e} ({args.fhir_dir})") from None
    if args.json:
        json.dump(records, sys.stdout, indent=1, ensure_ascii=False)
        print()
    else:
        sys.stdout.write(format_chart(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
