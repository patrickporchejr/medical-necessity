"""verify: resolve every assertion in a packet back to the chart.

Unsupported assertions are flagged for the reviewer, never dropped. An "evidence"
assertion is supported only if the chart establishes its criterion and every citation
resolves to a resource in this patient's chart that bears on that criterion. A "gap"
assertion is supported only if the chart really does not establish the criterion.
"""

from typing import Any, Protocol

from app.graph.criteria import Criteria, Criterion
from app.graph.state import (
    Assertion,
    CaseState,
    CitationCheck,
    CriterionEvidence,
    ResourceRef,
    Verification,
    VerifiedAssertion,
)
from app.graph.support import criterion_supports, established, record_status
from app.observability import node_span
from app.phi.gateway import ToolCallError


class ToolCaller(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _summarize(update: dict[str, Any]) -> dict[str, Any]:
    v = update["verification"]
    return {
        "assertions": len(v.assertions),
        "supported": len(v.assertions) - len(v.flagged),
        "flagged": len(v.flagged),
        "flagged_criteria": [a.assertion.criterion_id for a in v.flagged],
        "unaddressed": v.unaddressed,
    }


@node_span("graph.verify", _summarize)
async def verify(state: CaseState, gateway: ToolCaller, criteria: Criteria) -> dict[str, Any]:
    if state.packet is None:
        raise ValueError("verify needs a packet to check")
    by_id = {c.id: c for c in criteria.criteria}
    evidence = {e.criterion_id: e for e in state.evidence}

    verified = [
        await _verify_assertion(a, by_id.get(a.criterion_id), evidence.get(a.criterion_id),
                                state.patient_id, gateway)
        for a in state.packet.assertions
    ]
    mentioned = {a.criterion_id for a in state.packet.assertions}
    unaddressed = [c.id for c in criteria.criteria if c.id not in mentioned]
    return {"verification": Verification(assertions=verified, unaddressed=unaddressed)}


async def _verify_assertion(
    assertion: Assertion,
    criterion: Criterion | None,
    evidence: CriterionEvidence | None,
    patient_id: str,
    gateway: ToolCaller,
) -> VerifiedAssertion:
    if criterion is None or evidence is None:
        return VerifiedAssertion(
            assertion=assertion, supported=False, reasons=[f"unknown criterion {assertion.criterion_id!r}"]
        )

    reasons: list[str] = []
    checks: list[CitationCheck] = []
    holds = established(evidence)

    if assertion.kind == "gap":
        if holds:
            reasons.append("the chart does establish this criterion, so a gap claim is false")
        if assertion.citations:
            reasons.append("a gap claim has nothing to cite")
    else:
        if not assertion.citations:
            reasons.append("no citations")
        if not holds:
            reasons.append(f"the chart does not establish this criterion ({_why_not(evidence)})")
        for ref in assertion.citations:
            check = await _check_citation(ref, criterion, patient_id, gateway)
            checks.append(check)
            if not (check.exists and check.supports):
                reasons.append(f"{ref.resource_type} {ref.id}: {check.reason}")

    return VerifiedAssertion(
        assertion=assertion, supported=not reasons, checks=checks, reasons=reasons
    )


def _why_not(evidence: CriterionEvidence) -> str:
    if not evidence.found:
        return "no matching evidence"
    return f"duration {evidence.duration_status}"


async def _check_citation(
    ref: ResourceRef, criterion: Criterion, patient_id: str, gateway: ToolCaller
) -> CitationCheck:
    try:
        if ref.resource_type == "DocumentReference":
            record = await gateway.call_tool(
                "read_document", {"patient_id": patient_id, "document_id": ref.id}
            )
            code, text, status = None, record["text"], None
        else:
            record = await gateway.call_tool(
                "get_resource",
                {"patient_id": patient_id, "resource_type": ref.resource_type, "resource_id": ref.id},
            )
            code, text, status = record["code"]["code"], None, record_status(record)
    except (PermissionError, ToolCallError):
        # Not an id this case was ever shown, another patient's, or the wrong type.
        return CitationCheck(
            ref=ref, exists=False, supports=False, reason="not found in this patient's chart"
        )
    supports = criterion_supports(criterion, ref.resource_type, code, text, status)
    return CitationCheck(
        ref=ref, exists=True, supports=supports,
        reason=None if supports else f"does not bear on {criterion.id}",
    )
