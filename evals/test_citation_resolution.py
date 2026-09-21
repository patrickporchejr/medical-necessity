from datetime import date

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.config import settings
from app.graph.nodes.extract import extract
from app.graph.nodes.verify import verify
from app.graph.state import Assertion, CaseState, Packet, ResourceRef
from app.graph.support import established
from app.mcp.server import build_server
from app.phi.anonymize import Anonymizer
from app.phi.gateway import PhiGateway
from app.phi.rehydrate import rehydrate
from app.phi.vault import Vault
from dataset import open_ground_truth
from metrics.citation_resolution import score_packet

AS_OF = date(2026, 9, 20)


# --- the metric's arithmetic, against a stub truth ---------------------------------------------

class StubTruth:
    criterion_ids = ["a", "b", "c"]

    def __init__(self, established=("a", "b"), good=("x1", "x2", "x3")):
        self._established, self._good = set(established), set(good)

    def established(self, patient_id, criterion_id):
        return criterion_id in self._established

    def resource_supports(self, patient_id, criterion_id, resource_type, resource_id):
        return resource_id in self._good, resource_id in self._good


def ev(criterion, *ids):
    return Assertion(criterion_id=criterion, text="t", citations=[ResourceRef(resource_type="Condition", id=i) for i in ids])


def packet(*assertions):
    return Packet(service="s", assertions=list(assertions))


def test_all_resolved_scores_one():
    s = score_packet(packet(ev("a", "x1"), ev("b", "x2", "x3")), "p", StubTruth())
    assert (s.citation_resolution_rate, s.citation_level_rate) == (1.0, 1.0)
    assert s.unaddressed == ["c"]


def test_a_fabricated_or_missing_citation_fails_the_whole_assertion():
    s = score_packet(packet(ev("a", "x1"), ev("b", "x2", "ghost"), ev("a", "x3")), "p", StubTruth())
    assert s.citation_resolution_rate == pytest.approx(2 / 3)
    assert s.citation_level_rate == pytest.approx(3 / 4)
    assert score_packet(packet(ev("a")), "p", StubTruth()).citation_resolution_rate == 0.0  # uncited


def test_an_evidence_claim_for_an_unestablished_criterion_fails_even_with_a_real_citation():
    s = score_packet(packet(ev("c", "x1")), "p", StubTruth())
    assert s.citation_resolution_rate == 0.0 and s.citation_level_rate == 1.0


def test_gaps_are_scored_separately_and_never_inflate_the_headline():
    gap = lambda c: Assertion(criterion_id=c, kind="gap", text="none")
    s = score_packet(packet(gap("c"), gap("a")), "p", StubTruth())
    assert s.gap_accuracy == 0.5  # "c" truly is a gap; "a" is established
    assert s.citation_resolution_rate is None  # nothing to score, so not 100%


def test_unknown_criterion_never_resolves():
    s = score_packet(packet(ev("zzz", "x1")), "p", StubTruth())
    assert s.citation_resolution_rate == 0.0


# --- verify against ground truth on the real cohort --------------------------------------------

needs_cohort = pytest.mark.skipif(
    not any(settings.fhir_dir.glob("*.json")), reason="Synthea cohort not generated"
)


def pick_patients(truth):
    """One patient per situation the duration rule distinguishes."""
    picked = {}
    for case in truth.cases():
        if not truth.established(case.patient_id, "ra_diagnosis"):
            continue  # a resolved diagnosis makes an all-gap packet: nothing to corrupt (see test_run.py)
        chart = truth.store.chart(case.patient_id)
        mtx = [m for m in chart.medication_requests if m.code.code == "105585"]
        if not mtx:
            kind = "no order"
        elif truth.established(case.patient_id, "dmard_trial"):
            kind = "met"
        else:
            kind = "undetermined"
        picked.setdefault(kind, case)
    assert set(picked) == {"no order", "met", "undetermined"}
    return picked


def faithful_packet(evidence):
    """What a perfect assemble would write from extract's evidence."""
    out = []
    for e in evidence:
        if established(e):
            ref = (e.items[0] if e.items else e.excerpts[0]).ref
            out.append(Assertion(criterion_id=e.criterion_id, text=e.description, citations=[ref]))
        else:
            out.append(Assertion(criterion_id=e.criterion_id, kind="gap", text="not established"))
    return out


def replace(assertions, i, **changes):
    return [a.model_copy(update=changes) if n == i else a for n, a in enumerate(assertions)]


def indices(assertions, kind):
    return [n for n, a in enumerate(assertions) if a.kind == kind]


def seeded_faults(faithful, other_patient_ref):
    """Six ways an assemble step could go wrong, each applied to a packet that is otherwise right."""
    e1 = indices(faithful, "evidence")[0]
    g = indices(faithful, "gap")[0]
    cited = faithful[e1].citations
    other_criterion = next(a.criterion_id for a in faithful if a.criterion_id != faithful[e1].criterion_id)
    return {
        "fabricated id": replace(
            faithful, e1, citations=[ResourceRef(resource_type=cited[0].resource_type, id="<CONDITION_99>")]
        ),
        "another patient's resource": replace(faithful, e1, citations=[other_patient_ref]),
        "no citations": replace(faithful, e1, citations=[]),
        "cited for the wrong criterion": replace(faithful, e1, criterion_id=other_criterion),
        "evidence turned into a false gap": replace(faithful, e1, kind="gap", citations=[]),
        "gap turned into made-up evidence": replace(faithful, g, kind="evidence", citations=cited),
    }


@needs_cohort
@pytest.mark.anyio
async def test_verify_and_the_metric_agree_on_every_seeded_fault():
    truth = open_ground_truth(AS_OF)
    patients = pick_patients(truth)
    server = build_server(settings.fhir_dir, settings.criteria_dir)
    checked = 0

    async with create_connected_server_and_client_session(server._mcp_server) as session:
        for situation, case in patients.items():
            gateway = PhiGateway(session, Anonymizer(Vault()))
            state = CaseState(patient_id=await gateway.adopt_patient(case.patient_id))
            state = state.model_copy(update=await extract(state, gateway, truth.criteria, AS_OF))

            other = next(c for c in truth.cases() if c.patient_id != case.patient_id)
            other_state = CaseState(patient_id=await gateway.adopt_patient(other.patient_id))
            other_ev = (await extract(other_state, gateway, truth.criteria, AS_OF))["evidence"]

            faithful = faithful_packet(state.evidence)
            other_ref = next(x.items[0].ref for x in other_ev if x.criterion_id == "ra_diagnosis")
            cases = {"faithful": faithful, **seeded_faults(faithful, other_ref)}

            for label, assertions in cases.items():
                s = state.model_copy(update={"packet": Packet(service="s", assertions=assertions)})
                verdict = (await verify(s, gateway, truth.criteria))["verification"]
                real = Packet.model_validate(rehydrate(s.packet.model_dump(), gateway.anonymizer.vault, state.patient_id))
                score = score_packet(real, case.patient_id, truth)

                by_verify = [v.supported for v in verdict.assertions]
                by_truth = [a.resolved for a in score.assertions]
                assert by_verify == by_truth, (situation, label, by_verify, by_truth)
                assert verdict.unaddressed == score.unaddressed
                if label == "faithful":
                    assert all(by_truth), (situation, "a faithful packet must fully resolve")
                    assert score.citation_resolution_rate in (1.0, None)
                else:
                    assert not all(by_truth), (situation, label, "the seeded fault went undetected")
                checked += 1
    assert checked == 3 * 7  # three situations x (one faithful packet + six seeded faults)
