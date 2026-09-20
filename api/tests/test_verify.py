import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.config import settings
from app.graph.criteria import load_criteria
from app.graph.nodes.extract import extract
from app.graph.nodes.verify import verify
from app.graph.state import Assertion, CaseState, CriterionEvidence, Packet, ResourceRef
from app.mcp.server import build_server
from app.phi.anonymize import Anonymizer
from app.phi.gateway import PhiGateway
from app.phi.vault import Vault
from tests.test_extract import CRITERIA_FILE, ORDERED, _fhir_dir
from tests.test_mcp_tools import PID

PID2 = "bbbbbbbb-0000-0000-0000-000000000002"
OLD_NOTE, NEW_NOTE = "# Plan\nNothing to report.", "- Joint Pain\n- Joint Swelling"
ALL = ["ra_diagnosis", "dmard_trial", "active_disease", "tb_screening", "hepatitis_b_screening"]


@dataclass
class Case:
    state: CaseState
    gateway: PhiGateway
    criteria: object
    ev: dict[str, CriterionEvidence]
    other_patient_ra: ResourceRef  # a real resource belonging to a different patient

    async def verify(self, *assertions: Assertion):
        state = self.state.model_copy(update={"packet": Packet(service="s", assertions=list(assertions))})
        return (await verify(state, self.gateway, self.criteria))["verification"]

    def faithful(self) -> list[Assertion]:
        ev = self.ev
        return [
            Assertion(criterion_id="ra_diagnosis", text="RA", citations=[ev["ra_diagnosis"].items[0].ref]),
            Assertion(criterion_id="dmard_trial", text="MTX", citations=[ev["dmard_trial"].items[0].ref]),
            Assertion(criterion_id="active_disease", text="active", citations=[ev["active_disease"].excerpts[0].ref]),
            Assertion(criterion_id="tb_screening", kind="gap", text="no TB screening"),
            Assertion(criterion_id="hepatitis_b_screening", kind="gap", text="no HBV screening"),
        ]


@asynccontextmanager
async def case(tmp_path, mtx_status="active", days=120):
    fhir = _fhir_dir(tmp_path, [OLD_NOTE, NEW_NOTE], mtx_status)
    second = json.loads(next(fhir.glob("*.json")).read_text())
    for entry in second["entry"]:  # a second patient whose resource ids differ from the first's
        r = entry["resource"]
        r["id"] = PID2 if r["resourceType"] == "Patient" else r["id"] + "b"
    (fhir / f"Bob1_Second2_{PID2}.json").write_text(json.dumps(second))

    criteria = load_criteria(CRITERIA_FILE)
    as_of = ORDERED + timedelta(days=days)
    server = build_server(fhir, settings.criteria_dir)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        gateway = PhiGateway(session, Anonymizer(Vault()))
        state = CaseState(patient_id=await gateway.adopt_patient(PID))
        update = await extract(state, gateway, criteria, as_of)
        state = state.model_copy(update=update)
        other = CaseState(patient_id=await gateway.adopt_patient(PID2))
        other_ev = (await extract(other, gateway, criteria, as_of))["evidence"]
        yield Case(
            state, gateway, criteria,
            {e.criterion_id: e for e in state.evidence},
            other_ev[0].items[0].ref,
        )


def by_criterion(verification):
    return {v.assertion.criterion_id: v for v in verification.assertions}


@pytest.mark.anyio
async def test_a_faithful_packet_is_fully_supported(tmp_path):
    async with case(tmp_path) as c:
        v = await c.verify(*c.faithful())
    assert [a.supported for a in v.assertions] == [True] * 5
    assert v.flagged == [] and v.unaddressed == []


@pytest.mark.anyio
async def test_a_fabricated_citation_is_flagged_and_not_dropped(tmp_path):
    async with case(tmp_path) as c:
        packet = c.faithful()
        packet[0] = packet[0].model_copy(
            update={"citations": [ResourceRef(resource_type="Condition", id="<CONDITION_99>")]}
        )
        v = await c.verify(*packet)
    assert len(v.assertions) == 5  # nothing silently dropped
    ra = by_criterion(v)["ra_diagnosis"]
    assert not ra.supported and not ra.checks[0].exists
    assert [a.supported for a in v.assertions[1:]] == [True] * 4


@pytest.mark.anyio
async def test_another_patients_resource_does_not_resolve(tmp_path):
    async with case(tmp_path) as c:
        packet = c.faithful()
        packet[0] = packet[0].model_copy(update={"citations": [c.other_patient_ra]})
        v = await c.verify(*packet)
    ra = by_criterion(v)["ra_diagnosis"]
    assert not ra.supported and not ra.checks[0].exists


@pytest.mark.anyio
async def test_a_real_resource_cited_for_the_wrong_criterion_is_flagged(tmp_path):
    async with case(tmp_path) as c:
        ra_ref = c.ev["ra_diagnosis"].items[0].ref
        v = await c.verify(Assertion(criterion_id="dmard_trial", text="MTX", citations=[ra_ref]))
    [check] = v.assertions[0].checks
    assert check.exists and not check.supports and not v.assertions[0].supported


@pytest.mark.anyio
async def test_a_type_mismatched_citation_does_not_resolve(tmp_path):
    async with case(tmp_path) as c:
        cond = c.ev["ra_diagnosis"].items[0].ref
        wrong = ResourceRef(resource_type="Observation", id=cond.id)
        v = await c.verify(Assertion(criterion_id="ra_diagnosis", text="RA", citations=[wrong]))
    assert not v.assertions[0].checks[0].exists


@pytest.mark.anyio
async def test_an_evidence_claim_for_a_criterion_the_chart_cannot_support_is_flagged(tmp_path):
    async with case(tmp_path) as c:
        [weight] = await c.gateway.call_tool(
            "search_observations", {"patient_id": c.state.patient_id, "code": "29463-7", "limit": 1}
        )
        cited = ResourceRef(resource_type="Observation", id=weight["id"])
        made_up = Assertion(criterion_id="tb_screening", text="TB negative", citations=[cited])
        uncited = Assertion(criterion_id="hepatitis_b_screening", text="HBV negative")
        v = await c.verify(made_up, uncited)
    tb, hbv = v.assertions
    assert not tb.supported and tb.checks[0].exists and not tb.checks[0].supports
    assert any("does not establish" in r for r in tb.reasons)
    assert not hbv.supported and "no citations" in hbv.reasons


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status, days, supported, why",
    [("active", 120, True, None), ("active", 90, True, None),
     ("active", 60, False, "duration not_met"), ("completed", 400, False, "duration undetermined")],
)
async def test_dmard_claim_needs_the_duration_not_just_the_order(tmp_path, status, days, supported, why):
    async with case(tmp_path, mtx_status=status, days=days) as c:
        cited = c.ev["dmard_trial"].items[0].ref
        v = await c.verify(Assertion(criterion_id="dmard_trial", text="MTX 3 months", citations=[cited]))
    result = v.assertions[0]
    assert result.supported is supported
    assert result.checks[0].exists and result.checks[0].supports  # the citation itself is fine
    if why:
        assert any(why in r for r in result.reasons)


@pytest.mark.anyio
async def test_gap_claims_are_checked_in_both_directions(tmp_path):
    async with case(tmp_path) as c:
        ok = Assertion(criterion_id="tb_screening", kind="gap", text="no TB screening")
        false_gap = Assertion(criterion_id="ra_diagnosis", kind="gap", text="no RA diagnosis")
        citing = Assertion(
            criterion_id="hepatitis_b_screening", kind="gap", text="none",
            citations=[c.ev["ra_diagnosis"].items[0].ref],
        )
        v = await c.verify(ok, false_gap, citing)
    assert [a.supported for a in v.assertions] == [True, False, False]
    assert any("false" in r for r in v.assertions[1].reasons)


@pytest.mark.anyio
async def test_gap_for_a_criterion_with_an_undetermined_duration_is_supported(tmp_path):
    async with case(tmp_path, mtx_status="completed") as c:
        v = await c.verify(Assertion(criterion_id="dmard_trial", kind="gap", text="duration unknown"))
    assert v.assertions[0].supported


@pytest.mark.anyio
async def test_a_note_without_the_keywords_does_not_support_active_disease(tmp_path):
    async with case(tmp_path) as c:
        docs = await c.gateway.call_tool("list_documents", {"patient_id": c.state.patient_id})
        old = ResourceRef(resource_type="DocumentReference", id=docs[0]["id"])  # the note with nothing in it
        new = c.ev["active_disease"].excerpts[0].ref
        v = await c.verify(
            Assertion(criterion_id="active_disease", text="old", citations=[old]),
            Assertion(criterion_id="active_disease", text="new", citations=[new]),
        )
    assert [a.supported for a in v.assertions] == [False, True]
    assert v.assertions[0].checks[0].exists and not v.assertions[0].checks[0].supports


@pytest.mark.anyio
async def test_criteria_the_packet_never_mentions_are_reported(tmp_path):
    async with case(tmp_path) as c:
        v = await c.verify(c.faithful()[0])
    assert v.unaddressed == ALL[1:]


@pytest.mark.anyio
async def test_unknown_criterion_is_flagged(tmp_path):
    async with case(tmp_path) as c:
        v = await c.verify(Assertion(criterion_id="made_up", text="x", citations=[]))
    assert not v.assertions[0].supported and "unknown criterion" in v.assertions[0].reasons[0]


@pytest.mark.anyio
async def test_verify_requires_a_packet(tmp_path):
    async with case(tmp_path) as c:
        with pytest.raises(ValueError, match="packet"):
            await verify(c.state, c.gateway, c.criteria)
