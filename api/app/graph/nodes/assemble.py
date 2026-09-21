"""assemble: draft the packet from the evidence `extract` gathered.

The model decides, criterion by criterion, whether the evidence supports a claim and
which records to cite. It is not told the verdict: whether a claim holds is `verify`'s
job, and that check must stay independent of the model that made the claim. It does get
the facts code is better at than a model, such as how long an order has been in effect.
"""

import re
from typing import Any

from pydantic import BaseModel

from app.graph.criteria import Criteria
from app.graph.state import Assertion, CaseState, CriterionEvidence, Packet, ResourceRef
from app.llm.client import LLMClient
from app.llm.prompts.assemble import SYSTEM
from app.observability import agent_span, node_span, record_agent_usage


BARE_ID = re.compile(r"[A-Z]+_\d+")


def repair_citations(assertions: list[Assertion]) -> tuple[list[Assertion], int]:
    """Models sometimes drop the angle brackets: CONDITION_1 for <CONDITION_1>. That is the same
    id in a different typeface, not a fabrication, so restore it. Nothing else is touched: an id
    that is not a placeholder even after this stays as written and `verify` flags it. The count is
    kept, because how often a model needs this is worth knowing."""
    repaired, out = 0, []
    for assertion in assertions:
        cites = []
        for ref in assertion.citations:
            body = ref.id.strip().strip("<>").strip()
            fixed = f"<{body}>" if BARE_ID.fullmatch(body) else ref.id
            if fixed != ref.id:
                repaired += 1
                ref = ResourceRef(resource_type=ref.resource_type, id=fixed)
            cites.append(ref)
        out.append(assertion.model_copy(update={"citations": cites}))
    return out, repaired


class PacketDraft(BaseModel):
    """What the model returns; the node adds the service line."""

    assertions: list[Assertion]


def _summarize(update: dict[str, Any]) -> dict[str, Any]:
    assertions = update["packet"].assertions
    return {
        "assembled_by": update["assembled_by"],
        "assertions": len(assertions),
        "evidence_assertions": sum(a.kind == "evidence" for a in assertions),
        "gap_assertions": sum(a.kind == "gap" for a in assertions),
        "citations": sum(len(a.citations) for a in assertions),
        **update.get("llm_usage", {}),  # tokens, fallback, and how many ids needed repair
    }


@node_span("graph.assemble", _summarize)
async def assemble(state: CaseState, criteria: Criteria, llm: LLMClient) -> dict[str, Any]:
    with agent_span("assemble", llm.provider, "Drafts the prior authorization packet from the chart evidence") as agent:
        result = await llm.generate(SYSTEM, render_evidence(state, criteria), PacketDraft)
        record_agent_usage(agent, result)
    assertions, repaired = repair_citations(result.parsed.assertions)
    packet = Packet(service=state.service or criteria.service, assertions=assertions)
    return {
        "packet": packet,
        "assembled_by": f"{result.provider}:{result.model}",
        "llm_usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "fallback": result.fallback,
            "citations_repaired": repaired,
        },
    }


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
    if evidence is None or not (evidence.items or evidence.excerpts):
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
    if evidence is not None and evidence.required_status:
        out.append(f"  Required status: {evidence.required_status}.")
    if evidence is not None and evidence.min_duration_days is not None:
        out.append(
            f"  Minimum duration: {evidence.min_duration_days} days. "
            f"Duration status: {evidence.duration_status}."
        )
    return out
