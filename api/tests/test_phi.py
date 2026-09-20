import base64
import json
import pickle
import re
from datetime import date, timedelta

import pytest

from app.config import settings
from app.mcp import tools
from app.mcp.server import build_server
from app.mcp.store import FhirStore
from app.phi.anonymize import Anonymizer, UnclassifiedField
from app.phi.gateway import PhiGateway, ToolCallError
from app.phi.rehydrate import AmbiguousPatient, rehydrate
from app.phi.vault import Vault, VaultExpired
from tests.test_mcp_tools import PID, _bundle

NOTE = (
    "\n2022-03-01\n\n# Chief Complaint\n- Joint Swelling\n- Joint Stiffness\n\n"
    "Ada is a 52 year-old female with rheumatoid arthritis. Seen by Dr. Who Tardis on 2022-03-01, "
    "methotrexate started 2022-02-01. Call 415-555-0134 or ada@example.org, SSN 536-90-4399.\n"
    "# Social History\nPatient has never smoked. Lovelace was counselled.\n"
)


@pytest.fixture
def anon() -> Anonymizer:
    return Anonymizer(Vault())


def days(iso: str) -> date:
    return date.fromisoformat(iso[:10])


# --- vault ------------------------------------------------------------------------------------

def test_vault_placeholders_are_stable_and_reversible():
    v = Vault()
    a = v.placeholder("NAME", "Abel832")
    assert a == "<NAME_1>" and v.placeholder("NAME", "Abel832") == a
    assert v.placeholder("NAME", "Harvey63") == "<NAME_2>"
    assert v.placeholder("PATIENT", "Abel832") == "<PATIENT_1>"  # kinds count separately
    assert v.original(a) == "Abel832" and v.kind(a) == "NAME"
    assert v.original("<NAME_99>") is None


def test_vault_expires_and_forgets():
    now = [0.0]
    v = Vault(ttl_seconds=10, clock=lambda: now[0])
    token = v.placeholder("NAME", "Abel832")
    now[0] = 11
    with pytest.raises(VaultExpired):
        v.original(token)
    now[0] = 0  # even if the clock were wound back, the map is gone
    assert v.originals("NAME") == {}


def test_vault_cannot_be_serialized_or_leak_via_repr():
    v = Vault()
    v.placeholder("NAME", "Abel832")
    with pytest.raises(TypeError):
        pickle.dumps(v)
    assert "Abel832" not in repr(v)


def test_date_offset_is_stable_nonzero_and_bounded():
    v = Vault()
    offset = v.date_offset("p1")
    assert v.date_offset("p1") == offset
    assert 30 <= abs(offset.days) <= 730
    assert {v.date_offset(f"p{i}") for i in range(20)} != {offset}  # differs across patients


# --- structured scrubbing ---------------------------------------------------------------------

def _condition(**over):
    rec = {
        "id": "c1",
        "resource_type": "Condition",
        "patient_id": PID,
        "code": {"system": "http://snomed.info/sct", "code": "69896004", "display": "RA"},
        "clinical_status": "active",
        "verification_status": None,
        "onset_date": "2022-01-15T00:23:12+00:00",
        "recorded_date": "2022-01-15T00:23:12+00:00",
    }
    return rec | over


def test_ids_become_placeholders_and_codes_survive(anon):
    out = anon.scrub_result([_condition()])[0]
    assert out["id"] == "<CONDITION_1>" and out["patient_id"] == "<PATIENT_1>"
    assert out["code"]["code"] == "69896004" and out["clinical_status"] == "active"
    assert PID not in json.dumps(out)


def test_dates_shift_but_intervals_and_time_of_day_survive(anon):
    onset = _condition()
    order = {
        "id": "m1", "resource_type": "MedicationRequest", "patient_id": PID,
        "code": {"code": "105585", "display": "Methotrexate"}, "status": "active",
        "authored_on": "2022-04-20T00:23:12+00:00", "dosage": None, "reason": None,
    }
    o1, o2 = anon.scrub_result([onset, order])
    assert o1["onset_date"] != onset["onset_date"]
    assert o1["onset_date"].endswith("T00:23:12+00:00")
    assert days(o2["authored_on"]) - days(o1["onset_date"]) == timedelta(days=95)


def test_each_patient_gets_their_own_offset(anon):
    other = "bbbbbbbb-0000-0000-0000-000000000002"
    a = anon.scrub_result([_condition()])[0]
    b = anon.scrub_result([_condition(patient_id=other, id="c9")])[0]
    shifts = {days(r["onset_date"]) - days("2022-01-15") for r in (a, b)}
    assert len(shifts) == 2


def test_names_are_tokenized_and_titles_kept(anon):
    patient = {"id": PID, "resource_type": "Patient", "name": "Ada Byron Lovelace",
               "birth_date": "1970-01-02", "gender": "female"}
    out = anon.scrub_result(patient)
    assert out["name"] == "<NAME_1> <NAME_2> <NAME_3>"
    assert out["gender"] == "female" and out["birth_date"] != "1970-01-02"
    assert anon.register_name("Dr. Who Tardis") == "Dr. <NAME_4> <NAME_5>"


def test_unknown_fields_fail_closed(anon):
    with pytest.raises(UnclassifiedField, match="ssn"):
        anon.scrub_result([_condition(ssn="123")])
    with pytest.raises(UnclassifiedField):
        anon.scrub_result([_condition(code={"code": "1", "system": "x", "display": "y", "extra": "z"})])
    with pytest.raises(UnclassifiedField):
        anon.scrub_result({"something": "else"})


def test_record_without_patient_scope_is_rejected(anon):
    with pytest.raises(UnclassifiedField, match="patient scope"):
        anon.scrub_result([{"id": "x", "resource_type": "Condition"}])


# --- free text --------------------------------------------------------------------------------

def _note_record():
    return {
        "id": "d1", "resource_type": "DocumentReference", "patient_id": PID, "status": "current",
        "type": {"system": "http://loinc.org", "code": "34117-2", "display": "H&P"},
        "date": "2022-03-01T00:00:00+00:00", "author": "Dr. Who Tardis", "text": NOTE,
    }


def test_note_is_scrubbed_of_names_dates_and_contact_details(anon):
    anon.scrub_result({"id": PID, "resource_type": "Patient", "name": "Ada Lovelace",
                       "birth_date": "1970-01-02", "gender": "female"})
    text = anon.scrub_result(_note_record())["text"]
    for leaked in ("Ada", "Lovelace", "Who", "Tardis", "415-555-0134", "ada@example.org",
                   "536-90-4399", "2022-03-01", "2022-02-01"):
        assert leaked not in text, leaked
    # clinical content, including the phrases spaCy NER used to mangle, is untouched
    for kept in ("Joint Swelling", "Joint Stiffness", "rheumatoid arthritis", "methotrexate",
                 "# Social History\nPatient has never smoked", "52 year-old"):
        assert kept in text, kept


def test_dates_inside_notes_keep_their_spacing(anon):
    anon.scrub_result({"id": PID, "resource_type": "Patient", "name": "Ada Lovelace",
                       "birth_date": "1970-01-02", "gender": "female"})
    text = anon.scrub_result(_note_record())["text"]
    seen, started = (days(d) for d in re.findall(r"\d{4}-\d{2}-\d{2}", text)[1:3])
    assert seen - started == timedelta(days=28)


def test_name_match_is_case_insensitive_but_maps_to_one_placeholder(anon):
    anon.register_name("Abel832 Harvey63")
    out = anon.scrub_text("ABEL832 saw Abel832; harvey63 too", PID)
    assert out == "<NAME_1> saw <NAME_1>; <NAME_2> too"


def test_short_name_tokens_are_not_deny_listed(anon):
    anon.register_name("Al Li Abel832")
    assert anon.scrub_text("Al will see Li at the clinic.", PID) == "Al will see Li at the clinic."


def test_error_messages_are_masked(anon):
    anon.scrub_result([_condition()])
    anon.register_name("Abel832")
    msg = anon.scrub_message(f"Unknown patient_id: {PID}, Abel832, seen 2022-01-15")
    assert PID not in msg and "Abel832" not in msg and "2022-01-15" not in msg


# --- rehydration ------------------------------------------------------------------------------

def test_round_trip_restores_records_exactly(anon):
    records = [
        {"id": PID, "resource_type": "Patient", "name": "Ada Lovelace",
         "birth_date": "1970-01-02", "gender": "female"},
        _condition(),
        _note_record(),
    ]
    scrubbed = anon.scrub_result(records)
    assert scrubbed != records
    assert rehydrate(scrubbed, anon.vault) == records


def test_model_prose_is_rehydrated_including_dates(anon):
    scrubbed = anon.scrub_result([_condition()])[0]
    prose = f"Diagnosis <CONDITION_1> was recorded on {scrubbed['onset_date'][:10]}. See <NAME_9>."
    out = rehydrate(prose, anon.vault)
    assert out == "Diagnosis c1 was recorded on 2022-01-15. See <NAME_9>."  # unknown left alone


def test_rehydrate_needs_patient_when_vault_holds_several(anon):
    anon.scrub_result([_condition(), _condition(patient_id="other", id="c2")])
    with pytest.raises(AmbiguousPatient):
        rehydrate("2022-01-01", anon.vault)
    assert rehydrate("x", anon.vault, patient_id="<PATIENT_1>") == "x"


# --- gateway: the agent's view over the real MCP protocol -------------------------------------

@pytest.fixture
def phi_dir(tmp_path):
    bundle = _bundle()
    for entry in bundle["entry"]:
        if entry["resource"]["resourceType"] == "DocumentReference":
            entry["resource"]["content"][0]["attachment"]["data"] = base64.b64encode(NOTE.encode()).decode()
            entry["resource"]["author"] = [{"display": "Dr. Who Tardis"}]
    fhir = tmp_path / "fhir"
    fhir.mkdir()
    (fhir / f"Ada1_Lovelace2_{PID}.json").write_text(json.dumps(bundle))
    return fhir, tmp_path


@pytest.mark.anyio
async def test_gateway_hides_phi_from_the_agent(phi_dir):
    from mcp.shared.memory import create_connected_server_and_client_session

    fhir, criteria = phi_dir
    server = build_server(fhir, criteria)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        gateway = PhiGateway(session, Anonymizer(Vault()))
        patient = await gateway.adopt_patient(PID)
        assert patient == "<PATIENT_1>"

        seen = []  # everything the agent is ever shown
        conditions = await gateway.call_tool("search_conditions", {"patient_id": patient, "code": "69896004"})
        docs = await gateway.call_tool("list_documents", {"patient_id": patient})
        note = await gateway.call_tool("read_document", {"patient_id": patient, "document_id": docs[0]["id"]})
        obs = await gateway.call_tool("search_observations", {"patient_id": patient, "code": "29463-7", "limit": 2})
        roster = await gateway.call_tool("list_patients", {})
        seen += [conditions, docs, note, obs, roster]

        blob = json.dumps(seen)
        for secret in (PID, "Ada", "Lovelace", "Tardis", "415-555-0134", "ada@example.org", "536-90-4399",
                       "1970-01-02", "2022-03-01", "2022-02-01"):
            assert secret not in blob, secret
        assert conditions[0]["id"].startswith("<CONDITION_")
        assert "rheumatoid arthritis" in note["text"]

        # the reviewer side gets the real thing back
        restored = rehydrate(note, gateway.anonymizer.vault)
        assert restored["patient_id"] == PID and "2022-02-01" in restored["text"]
        assert rehydrate(conditions[0]["id"], gateway.anonymizer.vault) == "c1"


@pytest.mark.anyio
async def test_gateway_rejects_real_or_invented_ids_and_unknown_args(phi_dir):
    from mcp.shared.memory import create_connected_server_and_client_session

    fhir, criteria = phi_dir
    async with create_connected_server_and_client_session(build_server(fhir, criteria)._mcp_server) as session:
        gateway = PhiGateway(session, Anonymizer(Vault()))
        patient = await gateway.adopt_patient(PID)
        for bad in (
            {"patient_id": PID},                      # real id, not a placeholder
            {"patient_id": "<PATIENT_7>"},            # invented placeholder
            {"patient_id": "<NAME_1>"},               # wrong kind of placeholder
            {"patient_id": patient, "query": "x"},    # argument with no PHI rule
        ):
            with pytest.raises(PermissionError):
                await gateway.call_tool("search_conditions", bad)


@pytest.mark.anyio
async def test_gateway_tool_errors_are_scrubbed(phi_dir):
    from mcp.shared.memory import create_connected_server_and_client_session

    fhir, criteria = phi_dir
    async with create_connected_server_and_client_session(build_server(fhir, criteria)._mcp_server) as session:
        gateway = PhiGateway(session, Anonymizer(Vault()))
        patient = await gateway.adopt_patient(PID)
        vault = gateway.anonymizer.vault
        missing = vault.placeholder("DOCUMENTREFERENCE", "no-such-doc")
        with pytest.raises(ToolCallError) as err:
            await gateway.call_tool("read_document", {"patient_id": patient, "document_id": missing})
        assert PID not in str(err.value) and "no-such-doc" not in str(err.value)


@pytest.mark.anyio
async def test_gateway_shifts_a_real_date_onto_the_patients_timeline(phi_dir):
    from mcp.shared.memory import create_connected_server_and_client_session

    fhir, criteria = phi_dir
    async with create_connected_server_and_client_session(build_server(fhir, criteria)._mcp_server) as session:
        gateway = PhiGateway(session, Anonymizer(Vault()))
        patient = await gateway.adopt_patient(PID)
        [cond] = await gateway.call_tool("search_conditions", {"patient_id": patient, "code": "69896004"})
        shifted = gateway.shift_date(patient, "2022-01-01")  # c1's real onset date
        assert shifted == cond["onset_date"][:10]
        with pytest.raises(PermissionError):
            gateway.shift_date(PID, "2022-01-01")  # a real id is not accepted


# --- real cohort (skipped where it has not been generated) ------------------------------------

needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


@needs_cohort
def test_no_identifier_survives_in_any_note_of_the_cohort():
    store = FhirStore(settings.fhir_dir)
    iso = re.compile(r"\d{4}-\d{2}-\d{2}")
    for patient in tools.list_patients(store)[:8]:
        anon = Anonymizer(Vault())
        anon.scrub_result(tools.get_patient(store, patient.id).model_dump())
        for meta in tools.list_documents(store, patient.id):
            doc = tools.read_document(store, patient.id, meta.id)
            out = anon.scrub_result(doc.model_dump())
            names = {*patient.name.split(), *(t for t in (doc.author or "").split() if t not in {"Dr."})}
            assert not any(n in out["text"] or n in (out["author"] or "") for n in names), doc.id
            # every date is its original moved by the patient's offset (not merely "different":
            # a shifted date can coincide with another original date in the same chart)
            offset = anon.vault.date_offset(patient.id)
            expected = [(days(d) + offset).isoformat() for d in iso.findall(doc.text)]
            assert iso.findall(out["text"]) == expected, doc.id
            assert rehydrate(out, anon.vault) == doc.model_dump()
