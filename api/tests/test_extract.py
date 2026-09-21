import ast
import base64
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from app.config import settings
from app.graph.criteria import Criteria, load_criteria
from app.graph.nodes.extract import (
    MAX_EXCERPT_NOTES,
    MAX_LINES_PER_NOTE,
    MAX_NOTE_READS,
    extract,
    find_excerpt_lines,
)
from app.graph.state import CaseState
from app.mcp.server import build_server
from app.phi.anonymize import Anonymizer
from app.phi.gateway import PhiGateway
from app.phi.vault import Vault
from tests.test_mcp_tools import PID, _bundle

ORDERED = date(2022, 2, 1)  # the fixture's methotrexate order date
CRITERIA_FILE = settings.criteria_dir / "adalimumab_ra.yaml"
KEYWORDS = ["joint pain", "joint swelling", "joint stiffness"]


# --- criteria ---------------------------------------------------------------------------------

def test_real_criteria_file_loads():
    criteria = load_criteria(CRITERIA_FILE)
    assert [c.id for c in criteria.criteria] == [
        "ra_diagnosis", "dmard_trial", "active_disease", "tb_screening", "hepatitis_b_screening",
    ]
    dmard = criteria.criteria[1]
    assert dmard.min_duration_days == 90 and dmard.evidence[0].code.code == "105585"
    assert criteria.criteria[2].evidence[0].keywords == KEYWORDS


@pytest.mark.parametrize(
    "evidence",
    [
        [{"resource": "Observation"}],                    # needs a code
        [{"resource": "DocumentReference"}],              # needs keywords
        [{"resource": "Encounter", "code": {"system": "x", "code": "1"}}],  # unsupported resource
    ],
)
def test_invalid_evidence_specs_are_rejected(evidence):
    with pytest.raises(ValidationError):
        Criteria.model_validate(
            {"service": "s", "criteria": [{"id": "c", "description": "d", "evidence": evidence}]}
        )


# --- note excerpts ----------------------------------------------------------------------------

def test_excerpt_lines_match_keywords_case_insensitively_and_drop_bullets():
    text = "# Chief Complaint\n- Joint Swelling\n- Fatigue\n- joint PAIN\n\n# Plan\nNothing here."
    assert find_excerpt_lines(text, KEYWORDS) == ["Joint Swelling", "joint PAIN"]


def test_excerpt_lines_dedupe_and_cap():
    text = "\n".join(["- Joint Pain"] * 3 + [f"Joint swelling, site {i}" for i in range(10)])
    lines = find_excerpt_lines(text, KEYWORDS)
    assert lines.count("Joint Pain") == 1 and len(lines) == MAX_LINES_PER_NOTE


def test_long_paragraphs_are_cut_down_to_the_matching_sentence():
    filler = "Patient reports an unrelated history of many things. " * 6
    text = f"{filler}Joint stiffness worsened in the morning. {filler}"
    assert find_excerpt_lines(text, KEYWORDS) == ["Joint stiffness worsened in the morning."]


def test_no_match_gives_no_lines():
    assert find_excerpt_lines("# Plan\nFluoride application.", KEYWORDS) == []


# --- extract through the gateway --------------------------------------------------------------

def _note(text):
    return base64.b64encode(text.encode()).decode()


def _doc(n: int, text: str) -> dict:
    return {
        "resourceType": "DocumentReference",
        "id": f"note{n:03d}",
        "status": "current",
        "type": {"coding": [{"system": "http://loinc.org", "code": "34117-2", "display": "H&P"}]},
        "date": f"2022-{1 + n // 28:02d}-{1 + n % 28:02d}T00:00:00+00:00",
        "content": [{"attachment": {"contentType": "text/plain", "data": _note(text)}}],
    }


def _fhir_dir(tmp_path: Path, notes: list[str], mtx_status: str | None = None,
              ra_status: str | None = None) -> Path:
    bundle = _bundle()
    for e in bundle["entry"]:
        r = e["resource"]
        if ra_status and r["resourceType"] == "Condition" and r["id"] == "c1":
            r["clinicalStatus"] = {"coding": [{"code": ra_status}]}
        if mtx_status and e["resource"]["resourceType"] == "MedicationRequest":
            e["resource"]["status"] = mtx_status
    if mtx_status == "absent":  # a chart with no methotrexate order at all
        bundle["entry"] = [e for e in bundle["entry"] if e["resource"]["resourceType"] != "MedicationRequest"]
    bundle["entry"] = [
        e for e in bundle["entry"] if e["resource"]["resourceType"] != "DocumentReference"
    ]
    bundle["entry"] += [{"resource": _doc(i, t)} for i, t in enumerate(notes)]
    fhir = tmp_path / "fhir"
    fhir.mkdir()
    (fhir / f"Ada1_Lovelace2_{PID}.json").write_text(json.dumps(bundle))
    return fhir


class Counting:
    """Gateway proxy that records every tool call the node makes."""

    def __init__(self, gateway):
        self.gateway, self.calls = gateway, []

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        return await self.gateway.call_tool(name, arguments)

    def shift_date(self, patient_id, iso_date):
        return self.gateway.shift_date(patient_id, iso_date)


async def _run(tmp_path, notes, as_of=ORDERED + timedelta(days=120), mtx_status=None, ra_status=None):
    criteria = load_criteria(CRITERIA_FILE)
    server = build_server(_fhir_dir(tmp_path, notes, mtx_status, ra_status), settings.criteria_dir)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        gateway = PhiGateway(session, Anonymizer(Vault()))
        counting = Counting(gateway)
        patient = await gateway.adopt_patient(PID)
        update = await extract(CaseState(patient_id=patient), counting, criteria, as_of)
        return update, counting, gateway


@pytest.mark.anyio
async def test_extract_finds_supported_criteria_and_flags_unsupported_ones(tmp_path):
    update, _, gateway = await _run(tmp_path, ["# Plan\nNothing.", "- Joint Pain\n- Joint Swelling"])
    by_id = {e.criterion_id: e for e in update["evidence"]}

    assert update["service"].startswith("Adalimumab")
    assert by_id["ra_diagnosis"].items[0].label == "RA"
    assert by_id["dmard_trial"].items[0].status == "completed"
    [note] = by_id["active_disease"].excerpts
    assert note.lines == ["Joint Pain", "Joint Swelling"]
    assert gateway.anonymizer.vault.original(note.ref.id) == "note001"

    # The two screening criteria the chart cannot support: searched, found nothing.
    for cid in ("tb_screening", "hepatitis_b_screening"):
        assert not by_id[cid].found and by_id[cid].queries


@pytest.mark.anyio
async def test_extract_output_holds_no_real_identifiers(tmp_path):
    update, _, _ = await _run(tmp_path, ["- Joint Pain"])
    blob = json.dumps([e.model_dump() for e in update["evidence"]])
    for real in (PID, "c1", "m1", "note000", "2022-01-01", "2022-02-01", "2022-01-15"):
        assert real not in blob, real
    assert "<CONDITION_" in blob and "<DOCUMENTREFERENCE_" in blob


@pytest.mark.anyio
async def test_extract_reads_newest_notes_first_and_stops_at_the_excerpt_cap(tmp_path):
    update, counting, gateway = await _run(tmp_path, ["- Joint Pain"] * 12)
    [active] = [e for e in update["evidence"] if e.criterion_id == "active_disease"]
    real = [gateway.anonymizer.vault.original(x.ref.id) for x in active.excerpts]
    assert real == [f"note{i:03d}" for i in range(7, 12)]  # the newest five, oldest first
    assert counting.calls.count("read_document") == MAX_EXCERPT_NOTES


@pytest.mark.anyio
async def test_extract_caps_reads_on_a_large_chart_with_no_matches(tmp_path):
    update, counting, _ = await _run(tmp_path, ["# Plan\nFluoride."] * 80)
    [active] = [e for e in update["evidence"] if e.criterion_id == "active_disease"]
    assert not active.found
    assert counting.calls.count("read_document") == MAX_NOTE_READS


# --- a criterion can require a status: a resolved diagnosis is not an active one -------------

@pytest.mark.anyio
async def test_a_resolved_diagnosis_is_listed_but_does_not_qualify(tmp_path):
    update, _, _ = await _run(tmp_path, ["- Joint Pain"], ra_status="resolved")
    ra = next(e for e in update["evidence"] if e.criterion_id == "ra_diagnosis")
    assert ra.required_status == "active"
    assert [(i.status, i.qualifies) for i in ra.items] == [("resolved", False)]
    assert not ra.found  # so the criterion is not established
    from app.graph.support import established
    assert not established(ra)


@pytest.mark.anyio
async def test_an_active_diagnosis_qualifies(tmp_path):
    update, _, _ = await _run(tmp_path, ["- Joint Pain"])
    ra = next(e for e in update["evidence"] if e.criterion_id == "ra_diagnosis")
    assert [(i.status, i.qualifies) for i in ra.items] == [("active", True)] and ra.found


def test_the_ra_criterion_requires_an_active_status_and_notes_cannot_have_one():
    criteria = load_criteria(CRITERIA_FILE)
    assert criteria.criteria[0].evidence[0].status == "active"
    assert criteria.criteria[1].evidence[0].status is None  # the methotrexate spec has no status rule
    with pytest.raises(ValidationError):
        Criteria.model_validate({"service": "s", "criteria": [{"id": "c", "description": "d", "evidence": [
            {"resource": "DocumentReference", "keywords": ["pain"], "status": "active"}]}]})


def test_the_shared_predicate_checks_status():
    from app.graph.criteria import EvidenceSpec, CodeSpec
    from app.graph.support import spec_supports

    spec = EvidenceSpec(resource="Condition", code=CodeSpec(system="s", code="1"), status="active")
    assert spec_supports(spec, "Condition", "1", None, "active")
    assert not spec_supports(spec, "Condition", "1", None, "resolved")
    assert not spec_supports(spec, "Condition", "1", None, None)
    no_rule = EvidenceSpec(resource="Condition", code=CodeSpec(system="s", code="1"))
    assert spec_supports(no_rule, "Condition", "1", None, "resolved")  # no status rule: any status


# --- the as-of date and "at least 90 days of methotrexate" ------------------------------------

def _dmard(update):
    return next(e for e in update["evidence"] if e.criterion_id == "dmard_trial")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "days, expected", [(120, "met"), (90, "met"), (89, "not_met"), (0, "not_met")]
)
async def test_active_order_duration_is_measured_against_the_as_of_date(tmp_path, days, expected):
    # The chart's dates are shifted by a random per-patient offset; the answer must not be.
    update, _, _ = await _run(tmp_path, ["- Joint Pain"], ORDERED + timedelta(days=days), "active")
    dmard = _dmard(update)
    assert dmard.items[0].days_in_effect == days
    assert dmard.duration_status == expected


@pytest.mark.anyio
async def test_completed_order_has_unknown_duration_because_the_chart_has_no_stop_date(tmp_path):
    update, _, _ = await _run(tmp_path, ["- Joint Pain"], mtx_status="completed")
    dmard = _dmard(update)
    assert dmard.items[0].days_in_effect is None
    assert dmard.duration_status == "undetermined"


@pytest.mark.anyio
async def test_no_order_at_all_is_not_met(tmp_path):
    update, _, _ = await _run(tmp_path, ["- Joint Pain"], mtx_status="absent")
    dmard = _dmard(update)
    assert not dmard.found and dmard.duration_status == "not_met"


@pytest.mark.anyio
async def test_criteria_without_a_duration_requirement_report_none(tmp_path):
    update, _, _ = await _run(tmp_path, ["- Joint Pain"])
    by_id = {e.criterion_id: e for e in update["evidence"]}
    assert by_id["ra_diagnosis"].duration_status is None
    assert by_id["tb_screening"].duration_status is None


@pytest.mark.anyio
async def test_as_of_in_state_is_shifted_not_real(tmp_path):
    as_of = ORDERED + timedelta(days=100)
    update, _, _ = await _run(tmp_path, ["- Joint Pain"], as_of, "active")
    assert update["as_of"] != as_of.isoformat()
    dmard = _dmard(update)
    assert date.fromisoformat(update["as_of"]) - date.fromisoformat(dmard.items[0].date[:10]) == timedelta(days=100)


def test_as_of_setting_reads_the_env_var_and_defaults_to_today(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("AS_OF_DATE", raising=False)
    assert Settings(_env_file=None).as_of == date.today()
    monkeypatch.setenv("AS_OF_DATE", "2026-09-20")
    assert Settings(_env_file=None).as_of == date(2026, 9, 20)


# --- the boundary is structural ---------------------------------------------------------------

def test_graph_code_never_imports_the_mcp_server_or_client():
    graph_dir = Path(__file__).resolve().parents[1] / "app" / "graph"
    forbidden = ("mcp", "app.mcp")
    for path in graph_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in forbidden), (path, name)


# --- real cohort ------------------------------------------------------------------------------

needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


@needs_cohort
@pytest.mark.anyio
async def test_cohort_extract_matches_the_ground_truth_the_eval_will_use():
    from app.mcp import tools
    from app.mcp.store import FhirStore

    store = FhirStore(settings.fhir_dir)
    criteria = load_criteria(CRITERIA_FILE)
    server = build_server(settings.fhir_dir, settings.criteria_dir)
    as_of = date(2026, 9, 20)
    dmard_found = met = 0
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        for patient in tools.list_patients(store):
            gateway = PhiGateway(session, Anonymizer(Vault()))
            state = CaseState(patient_id=await gateway.adopt_patient(patient.id))
            evidence = {e.criterion_id: e for e in (await extract(state, gateway, criteria, as_of))["evidence"]}

            orders = tools.search_medication_requests(store, patient.id, "105585")
            has_mtx = bool(orders)
            ra_active = any(c.code.code == "69896004" and c.clinical_status == "active"
                            for c in store.chart(patient.id).conditions)
            assert evidence["ra_diagnosis"].found == ra_active, patient.id
            assert evidence["dmard_trial"].found == has_mtx, patient.id
            assert not evidence["tb_screening"].found and not evidence["hepatitis_b_screening"].found
            dmard_found += has_mtx

            # Expected status from the raw, unshifted chart.
            if not orders:
                expected = "not_met"
            elif any(o.status == "active" and (as_of - date.fromisoformat(o.authored_on[:10])).days >= 90
                     for o in orders):
                expected = "met"
            elif any(o.status != "active" for o in orders):
                expected = "undetermined"
            else:
                expected = "not_met"
            assert evidence["dmard_trial"].duration_status == expected, patient.id
            met += expected == "met"
    assert dmard_found == 19
    print(f"dmard_trial duration met for {met} of 34 patients as of {as_of}")
