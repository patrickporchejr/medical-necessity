import base64
import json
from pathlib import Path

import pytest

from app.config import settings
from app.mcp import tools
from app.mcp.store import FhirStore, NotFound

PID = "aaaaaaaa-0000-0000-0000-000000000001"
NOTE = "Chief complaint: joint pain and swelling."


def _bundle() -> dict:
    subject = {"reference": f"urn:uuid:{PID}"}
    resources = [
        {
            "resourceType": "Patient",
            "id": PID,
            "name": [{"use": "official", "given": ["Ada"], "family": "Lovelace"}],
            "birthDate": "1970-01-02",
            "gender": "female",
        },
        {
            "resourceType": "Condition",
            "id": "c1",
            "subject": subject,
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "69896004"}], "text": "RA"},
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "onsetDateTime": "2022-01-01T00:00:00+00:00",
        },
        {
            "resourceType": "Condition",
            "id": "c0",
            "subject": subject,
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "38341003"}]},
            "onsetDateTime": "2010-01-01T00:00:00+00:00",
        },
        {
            "resourceType": "MedicationRequest",
            "id": "m1",
            "status": "completed",
            "subject": subject,
            "medicationCodeableConcept": {"coding": [{"system": "rxnorm", "code": "105585", "display": "Methotrexate"}]},
            "authoredOn": "2022-02-01T00:00:00+00:00",
            "reasonReference": [{"reference": "urn:uuid:c1", "display": "Rheumatoid arthritis"}],
        },
        *[
            {
                "resourceType": "Observation",
                "id": f"o{i}",
                "status": "final",
                "subject": subject,
                "code": {"coding": [{"system": "http://loinc.org", "code": "29463-7"}]},
                "effectiveDateTime": f"2022-0{i}-01T00:00:00+00:00",
                "valueQuantity": {"value": 70 + i, "unit": "kg"},
            }
            for i in (3, 1, 2)
        ],
        {
            "resourceType": "DocumentReference",
            "id": "d1",
            "status": "current",
            "type": {"coding": [{"system": "http://loinc.org", "code": "34117-2", "display": "H&P"}]},
            "date": "2022-03-01T00:00:00+00:00",
            "author": [{"display": "Dr. Who"}],
            "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(NOTE.encode()).decode()}}],
        },
    ]
    return {"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]}


@pytest.fixture
def fhir_dir(tmp_path: Path) -> Path:
    (tmp_path / f"Ada1_Lovelace2_{PID}.json").write_text(json.dumps(_bundle()))
    (tmp_path / "hospitalInformation1.json").write_text("{}")
    (tmp_path / "practitionerInformation1.json").write_text("{}")
    return tmp_path


@pytest.fixture
def store(fhir_dir: Path) -> FhirStore:
    return FhirStore(fhir_dir)


def test_list_patients_skips_non_patient_bundles(store):
    [patient] = tools.list_patients(store)
    assert (patient.id, patient.name, patient.gender) == (PID, "Ada Lovelace", "female")


def test_conditions_filter_by_code_and_sort_oldest_first(store):
    assert [c.id for c in tools.search_conditions(store, PID)] == ["c0", "c1"]
    [ra] = tools.search_conditions(store, PID, "69896004")
    assert (ra.id, ra.resource_type, ra.patient_id, ra.clinical_status) == ("c1", "Condition", PID, "active")


def test_medication_requests(store):
    [mtx] = tools.search_medication_requests(store, PID, "105585")
    assert (mtx.id, mtx.status, mtx.reason) == ("m1", "completed", "Rheumatoid arthritis")
    assert tools.search_medication_requests(store, PID, "nope") == []


def test_observations_require_code(store):
    with pytest.raises(ValueError, match="code is required"):
        tools.search_observations(store, PID, "")


def test_observations_limit_keeps_most_recent_in_date_order(store):
    obs = tools.search_observations(store, PID, "29463-7", limit=2)
    assert [(o.effective_date[:7], o.value, o.unit) for o in obs] == [("2022-02", 72, "kg"), ("2022-03", 73, "kg")]
    assert tools.search_observations(store, PID, "71774-4") == []


def test_documents_list_and_read(store):
    [meta] = tools.list_documents(store, PID)
    assert (meta.id, meta.type.code, meta.author) == ("d1", "34117-2", "Dr. Who")
    doc = tools.read_document(store, PID, "d1")
    assert doc.text == NOTE and doc.id == "d1"


def test_unknown_ids_raise(store):
    with pytest.raises(NotFound):
        tools.search_conditions(store, "missing")
    with pytest.raises(NotFound):
        tools.read_document(store, PID, "missing")


def test_document_ids_are_scoped_per_patient(tmp_path):
    other = "bbbbbbbb-0000-0000-0000-000000000002"
    bundle = _bundle()
    for entry in bundle["entry"]:
        if entry["resource"]["resourceType"] == "Patient":
            entry["resource"]["id"] = other
    (tmp_path / f"A_B_{PID}.json").write_text(json.dumps(_bundle()))
    (tmp_path / f"C_D_{other}.json").write_text(json.dumps(bundle))
    store = FhirStore(tmp_path)
    # Both charts contain a document with id "d1"; the patient_id decides which one.
    assert tools.read_document(store, PID, "d1").patient_id == PID
    assert tools.read_document(store, other, "d1").patient_id == other


# --- wire level: the contract as the agent sees it -------------------------------------------

@pytest.mark.anyio
async def test_contract_over_mcp_protocol(fhir_dir, tmp_path):
    from mcp.shared.memory import create_connected_server_and_client_session

    from app.mcp.server import build_server

    criteria = tmp_path / "criteria"
    criteria.mkdir()
    (criteria / "demo.yaml").write_text("service: demo\n")
    server = build_server(fhir_dir, criteria)

    async with create_connected_server_and_client_session(server._mcp_server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {
            "list_patients",
            "get_patient",
            "search_conditions",
            "search_medication_requests",
            "search_observations",
            "list_documents",
            "read_document",
        }

        result = await client.call_tool("search_conditions", {"patient_id": PID, "code": "69896004"})
        assert not result.isError
        [ra] = result.structuredContent["result"]
        assert ra["id"] == "c1" and ra["resource_type"] == "Condition"

        missing = await client.call_tool("search_conditions", {"patient_id": "missing"})
        assert missing.isError

        assert (await client.read_resource("criteria://demo")).contents[0].text == "service: demo\n"
        with pytest.raises(Exception):
            await client.read_resource("criteria://..%2Fsecrets")


# --- real Synthea cohort (skipped where the data has not been generated) ---------------------

needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


@pytest.fixture(scope="module")
def cohort() -> FhirStore:
    return FhirStore(settings.fhir_dir)


@needs_cohort
def test_cohort_every_patient_has_ra_and_19_have_methotrexate(cohort):
    patients = tools.list_patients(cohort)
    assert len(patients) == 34
    with_ra = [p for p in patients if tools.search_conditions(cohort, p.id, "69896004")]
    with_mtx = [p for p in patients if tools.search_medication_requests(cohort, p.id, "105585")]
    assert (len(with_ra), len(with_mtx)) == (34, 19)


@needs_cohort
def test_cohort_notes_decode_to_text(cohort):
    patient = tools.list_patients(cohort)[0]
    docs = tools.list_documents(cohort, patient.id)
    assert docs and all(d.type for d in docs)
    text = tools.read_document(cohort, patient.id, docs[-1].id).text
    assert "Chief Complaint" in text


@needs_cohort
def test_cohort_screening_labs_are_absent_by_design(cohort):
    # tb_screening / hepatitis_b_screening cannot be supported from this chart; verify must flag them.
    for p in tools.list_patients(cohort):
        assert tools.search_observations(cohort, p.id, "71774-4") == []
        assert tools.search_observations(cohort, p.id, "5195-3") == []
