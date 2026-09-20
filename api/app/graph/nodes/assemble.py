"""assemble: draft the packet from the evidence `extract` gathered.

The model decides, criterion by criterion, whether the evidence supports a claim and
which records to cite. It is not told the verdict: whether a claim holds is `verify`'s
job, and that check must stay independent of the model that made the claim. It does get
the facts code is better at than a model, such as how long an order has been in effect.
"""

from typing import Any

from pydantic import BaseModel

from app.graph.criteria import Criteria
from app.graph.state import Assertion, CaseState, CriterionEvidence, Packet
from app.llm.client import LLMClient
from app.llm.prompts.assemble import SYSTEM


class PacketDraft(BaseModel):
    """What the model returns; the node adds the service line."""

    assertions: list[Assertion]


async def assemble(state: CaseState, criteria: Criteria, llm: LLMClient) -> dict[str, Any]:
    result = await llm.generate(SYSTEM, render_evidence(state, criteria), PacketDraft)
    packet = Packet(service=state.service or criteria.service, assertions=result.parsed.assertions)
    return {"packet": packet, "assembled_by": f"{result.provider}:{result.model}"}


def render_evidence(state: CaseState, criteria: Criteria) -> str:
    by_id = {e.criterion_id: e for e in state.evidence}
    lines = [f"Service requested: {criteria.service}"]
    if state.as_of:
        lines.append(f"As of: {state.as_of}")
    for criterion in criteria.criteria:
        lines += ["", f"Criterion {criterion.id}: {' '.join(criterion.description.split())}"]
        lines += _render_evidence(by_id.get(criterion.id))
    return "\n".join(lines)


def _render_evidence(evidence: CriterionEvidence | None) -> list[str]:
    if evidence is None or not evidence.found:
        out = ["  No matching records were found in the chart."]
    else:
        out = ["  Evidence:"]
        for item in evidence.items:
            parts = [item.label, f"status {item.status}" if item.status else None,
                     f"dated {item.date[:10]}" if item.date else None,
                     f"in effect for {item.days_in_effect} days" if item.days_in_effect is not None else None]
            out.append(f"  - {item.ref.resource_type} {item.ref.id}: " + "; ".join(p for p in parts if p))
        for note in evidence.excerpts:
            when = f" dated {note.date[:10]}" if note.date else ""
            quoted = " | ".join(f'"{line}"' for line in note.lines)
            out.append(f"  - {note.ref.resource_type} {note.ref.id}{when}, mentions: {quoted}")
    if evidence is not None and evidence.min_duration_days is not None:
        out.append(
            f"  Minimum duration: {evidence.min_duration_days} days. "
            f"Duration status: {evidence.duration_status}."
        )
    return out
