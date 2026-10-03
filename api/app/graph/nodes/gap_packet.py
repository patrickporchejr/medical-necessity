"""gap_packet: write the packet without a model, for a case that fails its gate criterion.

The graph routes here when `extract` finds the diagnosis the whole policy rests on is not
established. No packet can be approved without it, so drafting one with a model would spend
a call to say what code already knows. Every assertion is written from the evidence and the
same `established` rule `verify` checks against: a gap where the chart falls short (always
including the gate), and, should another criterion be met, an evidence assertion citing the
records that meet it, since a gap claim there would be false.
"""

from typing import Any

from app.graph.criteria import Criteria
from app.graph.state import Assertion, CaseState, CriterionEvidence, Packet
from app.graph.support import established
from app.observability import node_span


def _summarize(update: dict[str, Any]) -> dict[str, Any]:
    assertions = update["packet"].assertions
    return {
        "route": update["route"],
        "assertions": len(assertions),
        "evidence_assertions": sum(a.kind == "evidence" for a in assertions),
        "gap_assertions": sum(a.kind == "gap" for a in assertions),
        "citations": sum(len(a.citations) for a in assertions),
    }


@node_span("graph.gap_packet", _summarize)
async def gap_packet(state: CaseState, criteria: Criteria, gate: str) -> dict[str, Any]:
    by_id = {e.criterion_id: e for e in state.evidence}
    assertions = [_assertion(c.id, by_id.get(c.id), gate) for c in criteria.criteria]
    return {
        "packet": Packet(service=state.service or criteria.service, assertions=assertions),
        "route": "gap_packet",
    }


def _assertion(criterion_id: str, evidence: CriterionEvidence | None, gate: str) -> Assertion:
    if evidence is not None and established(evidence):
        qualifying = [i for i in evidence.items if i.qualifies]
        long_enough = [i for i in qualifying if evidence.min_duration_days is None or (
            i.days_in_effect is not None and i.days_in_effect >= evidence.min_duration_days)]
        cites = [i.ref for i in long_enough or qualifying] + [n.ref for n in evidence.excerpts]
        return Assertion(criterion_id=criterion_id, text="Shown by the cited records.", citations=cites)
    text = _shortfall(evidence)
    if criterion_id == gate:
        text += " This criterion is required for any approval, so no model drafted this packet."
    return Assertion(criterion_id=criterion_id, kind="gap", text=text)


def _shortfall(evidence: CriterionEvidence | None) -> str:
    if evidence is None or not (evidence.items or evidence.excerpts):
        return "No matching records were found in the chart."
    if not evidence.found:
        return f"The chart has matching records, but none with status {evidence.required_status}."
    return (f"The chart does not show the minimum duration of {evidence.min_duration_days} days "
            f"(duration {evidence.duration_status}).")
