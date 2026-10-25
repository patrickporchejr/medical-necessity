import base64
import json
import re
from pathlib import Path

import pytest

import chart
from chart import chart_records, find_patient, format_chart, main
from app.config import settings
from app.mcp.store import FhirStore

PATIENT_ID = "c0ffee00-1111-2222-3333-444455556666"
KEY = PATIENT_ID[:8]
NAME = {"use": "official", "given": ["Rosa123", "Lee45"], "family": "Okafor678", "prefix": ["Ms."]}
DEMOGRAPHICS = ["Rosa123", "Lee45", "Okafor678", "1971-02-03", "17 Elm Street", "Dracut",
                "999-11-2222", "555-123-4567", PATIENT_ID]
NOTES = {
    # The later note comes first in the bundle, so sorting is exercised.
    "doc-2": ("2024-05-01T09:00:00Z", "\n2024-05-01\n\n# HPI\nRosa123 Lee45 Okafor678 is a 53 year-old female.\n"
              "Rosa123 reports morning stiffness of 90 minutes. Swollen MCP joints on exam.\n"),
    "doc-1": ("2019-03-02T09:00:00Z", "\n2019-03-02\n\n# HPI\nMs. Okafor678 denies joint pain.\n"),
}


def _note(doc_id: str, date: str, text: str) -> dict:
    return {"resourceType": "DocumentReference", "id": doc_id, "status": "current", "date": date,
            "type": {"coding": [{"system": "http://loinc.org", "code": "34117-2", "display": "H&P note"}]},
            "author": [{"display": "Dr. Ann999 Smith888"}],
            "content": [{"attachment": {"data": base64.b64encode(text.encode()).decode()}}]}


@pytest.fixture
def fhir_dir(tmp_path: Path) -> Path:
    resources = [
        {"resourceType": "Patient", "id": PATIENT_ID, "name": [NAME], "birthDate": "1971-02-03",
         "gender": "female", "address": [{"line": ["17 Elm Street"], "city": "Dracut"}],
         "identifier": [{"value": "999-11-2222"}], "telecom": [{"system": "phone", "value": "555-123-4567"}]},
        {"resourceType": "Condition", "id": "cond-2", "onsetDateTime": "2020-01-05T00:00:00Z",
         "clinicalStatus": {"coding": [{"code": "active"}]},
         "code": {"coding": [{"system": "http://snomed.info/sct", "code": "69896004",
                              "display": "Rheumatoid arthritis (disorder)"}]}},
        {"resourceType": "Condition", "id": "cond-1", "onsetDateTime": "2001-07-09T00:00:00Z",
         "clinicalStatus": {"coding": [{"code": "resolved"}]},
         "code": {"coding": [{"system": "http://snomed.info/sct", "code": "80967001", "display": "Dental caries"}]}},
        {"resourceType": "MedicationRequest", "id": "med-1", "status": "stopped", "authoredOn": "2020-02-01T00:00:00Z",
         "medicationCodeableConcept": {"coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                                   "code": "105585", "display": "Methotrexate 2.5 MG Oral Tablet"}]},
         "dosageInstruction": [{"text": "weekly"}]},
        {"resourceType": "Observation", "id": "obs-1", "status": "final", "effectiveDateTime": "2021-04-04T00:00:00Z",
         "code": {"coding": [{"system": "http://loinc.org", "code": "4537-7", "display": "ESR"}]},
         "valueQuantity": {"value": 41, "unit": "mm/h"}},
        *(_note(doc_id, date, text) for doc_id, (date, text) in NOTES.items()),
    ]
    bundle = {"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]}
    (tmp_path / f"Rosa123_Okafor678_{PATIENT_ID}.json").write_text(json.dumps(bundle))
    return tmp_path


def run(capsys, *argv: str) -> str:
    assert main(list(argv)) == 0
    return capsys.readouterr().out


def redacted(text: str) -> str:
    return re.sub(r"Rosa123 Lee45 Okafor678|Rosa123|Lee45|Okafor678", "[patient]", text)


def test_no_demographics_in_either_view(fhir_dir, capsys):
    for out in (run(capsys, KEY, "--fhir-dir", str(fhir_dir)),
                run(capsys, KEY, "--fhir-dir", str(fhir_dir), "--json")):
        assert [d for d in DEMOGRAPHICS if d in out] == []
        assert "female" in out  # inside the note text only; there is no gender field
        assert "Ann999" not in out  # note authors are left out


def test_every_note_is_present_in_full_with_only_the_name_replaced(fhir_dir, capsys):
    text = run(capsys, KEY, "--fhir-dir", str(fhir_dir))
    notes = json.loads(run(capsys, KEY, "--fhir-dir", str(fhir_dir), "--json"))["notes"]
    assert [n["ref"] for n in notes] == ["DocumentReference/doc-1", "DocumentReference/doc-2"]
    for note in notes:
        original = NOTES[note["ref"].split("/")[1]][1]
        assert note["text"] == redacted(original)
        assert note["text"].strip("\n") in text
    assert "[patient] is a 53 year-old female" in text and "Ms. [patient] denies joint pain" in text


def test_json_round_trips_the_records_the_text_view_prints(fhir_dir, capsys):
    out = run(capsys, KEY, "--fhir-dir", str(fhir_dir), "--json")
    records = json.loads(out)
    store = FhirStore(fhir_dir)
    assert records == chart_records(store.chart(find_patient(store, KEY)), KEY)
    assert format_chart(records) == run(capsys, KEY, "--fhir-dir", str(fhir_dir))
    assert list(records) == ["key", *chart.SECTIONS]
    every = [r for s in chart.SECTIONS for r in records[s]]
    assert all(list(r) == ["ref", "date", "status", "summary", "code", "text"] for r in every)
    text = format_chart(records)
    assert [r["ref"] for r in every] == re.findall(r"(?:Condition|MedicationRequest|Observation|DocumentReference)/"
                                                    r"[\w-]+(?=  )", text)
    assert records["conditions"][0] == {"ref": "Condition/cond-1", "date": "2001-07-09", "status": "resolved",
                                        "summary": "Dental caries", "code": "SNOMED 80967001", "text": None}
    assert records["medication_requests"][0]["summary"] == "Methotrexate 2.5 MG Oral Tablet; dosage: weekly"
    assert records["observations"][0]["summary"] == "ESR: 41.0 mm/h"  # the store keeps the value as a float


def test_each_section_is_sorted_oldest_first(fhir_dir):
    store = FhirStore(fhir_dir)
    records = chart_records(store.chart(PATIENT_ID), KEY)
    for section in chart.SECTIONS:
        dates = [r["date"] for r in records[section]]
        assert dates == sorted(dates)
    assert [r["ref"] for r in records["conditions"]] == ["Condition/cond-1", "Condition/cond-2"]


@pytest.mark.parametrize("key, message", [
    ("deadbeef", "no case with key deadbeef"),
    ("c0ffee", "a case key is 8 characters"),
])
def test_an_unknown_key_errors_clearly(fhir_dir, key, message):
    with pytest.raises(SystemExit) as exc:
        main([key, "--fhir-dir", str(fhir_dir)])
    assert message in str(exc.value) and str(fhir_dir) in str(exc.value)


def test_sizes_estimates_every_listed_key_and_the_total(fhir_dir, tmp_path, monkeypatch, capsys):
    keys_file = tmp_path / "held_out.txt"
    keys_file.write_text(f"# method\n{KEY}\n")
    monkeypatch.setattr(chart, "HELD_OUT_FILE", keys_file)
    out = run(capsys, "--sizes", "--fhir-dir", str(fhir_dir))
    expected = chart.estimate_tokens(run(capsys, KEY, "--fhir-dir", str(fhir_dir)))
    assert "an estimate, not a count" in out
    assert re.search(rf"^  {KEY}\s+{expected:,}$", out, re.M)
    assert re.search(rf"^  total\s+{expected:,}  \(1 charts\)$", out, re.M)


def test_the_viewer_imports_nothing_from_the_pipeline():
    source = Path(chart.__file__).read_text()
    assert not re.search(r"^\s*(from|import)\s+(app\.graph|app\.llm|dataset|experiment)\b", source, re.M)


needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


@needs_cohort
@pytest.mark.cohort
def test_a_held_out_chart_shows_every_note_in_the_bundle(capsys):
    key = chart.load_keys()[0]
    store = FhirStore(settings.fhir_dir)
    records = json.loads(run(capsys, key, "--json"))
    bundle = json.loads(store._paths[find_patient(store, key)].read_text())
    with_text = [e["resource"]["id"] for e in bundle["entry"]
                 if e["resource"]["resourceType"] == "DocumentReference"]
    assert sorted(n["ref"].split("/")[1] for n in records["notes"]) == sorted(with_text)
