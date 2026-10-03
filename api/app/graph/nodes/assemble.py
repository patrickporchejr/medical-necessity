"""assemble: draft the packet from the evidence `extract` gathered.

The model decides, criterion by criterion, whether the evidence supports a claim and
which records to cite. It is not told the verdict: whether a claim holds is `verify`'s
job, and that check must stay independent of the model that made the claim. It does get
the facts code is better at than a model, such as how long an order has been in effect.

On a repair (the graph sends a flagged packet back here) the model sees only the criteria
verify flagged or found unaddressed: their evidence, what it wrote for them, and verify's
reasons. Its new assertions replace those criteria's and nothing else, so a supported
assertion is never put at risk by a second draw. verify's reasons are its verdict, so for the
repaired criteria verify is no longer an independent check. That is why the first packet is
kept, and evals score it as well as the final one.
"""

import re
from dataclasses import replace
from typing import Any, Callable

from pydantic import BaseModel

from app.graph.criteria import Criteria
from app.graph.state import Assertion, CaseState, CriterionEvidence, Packet, ResourceRef, Verification
from app.llm.client import LLMClient, LLMResult, LLMSchemaError
from app.llm.prompts.assemble import SYSTEM
from app.observability import agent_span, node_span, record_agent_usage


BARE_ID = re.compile(r"[A-Z]+_\d+")
MAX_ERROR_CHARS = 2000  # of the validation error fed back: enough to say what was wrong


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
        "route": update["route"],
        "assembled_by": update["assembled_by"],
        "assertions": len(assertions),
        "evidence_assertions": sum(a.kind == "evidence" for a in assertions),
        "gap_assertions": sum(a.kind == "gap" for a in assertions),
        "citations": sum(len(a.citations) for a in assertions),
        "repairs": update.get("repairs", 0),  # 0 on the first draft, 1 on the first repair
        **update.get("llm_usage", {}),  # tokens, fallback, and how many ids needed repair
    }


@node_span("graph.assemble", _summarize)
async def assemble(
    state: CaseState, criteria: Criteria, llm: LLMClient, scrub: Callable[[str], str] = lambda text: text
) -> dict[str, Any]:
    """`scrub` cleans a validation error before it goes back to the model: the error can quote
    the model's own output. The graph passes the gateway's scrubber."""
    if state.verification is not None:
        return await _repair(state, criteria, llm, scrub)
    with agent_span("assemble", llm.provider, "Drafts the prior authorization packet from the chart evidence") as agent:
        result, retries = await _generate(llm, render_evidence(state, criteria), scrub)
        record_agent_usage(agent, result)
    assertions, repaired = repair_citations(result.parsed.assertions)
    packet = Packet(service=state.service or criteria.service, assertions=assertions)
    return {
        "packet": packet,
        "route": "assemble",
        "assembled_by": f"{result.provider}:{result.model}",
        "llm_usage": {
            "input_tokens": result.input_tokens,  # over every attempt
            "output_tokens": result.output_tokens,
            "fallback": result.fallback,
            "citations_repaired": repaired,
            "schema_retries": retries,
        },
    }


def repair_targets(state: CaseState, criteria: Criteria) -> list[str]:
    """The criteria a repair would redo: those with a flagged assertion or none at all, in the
    criteria file's order. A flagged assertion naming no known criterion has no evidence to
    redraft from, so it is left flagged rather than repaired."""
    v = state.verification
    if v is None:
        return []
    flagged = {a.assertion.criterion_id for a in v.flagged} | set(v.unaddressed)
    return [c.id for c in criteria.criteria if c.id in flagged]


async def _repair(
    state: CaseState, criteria: Criteria, llm: LLMClient, scrub: Callable[[str], str]
) -> dict[str, Any]:
    targets = repair_targets(state, criteria)
    with agent_span("assemble", llm.provider, "Redrafts the criteria verify flagged") as agent:
        result, retries = await _generate(llm, render_repair(state, criteria, targets), scrub)
        record_agent_usage(agent, result)
    redrafted, repaired = repair_citations(result.parsed.assertions)
    packet = state.packet.model_copy(update={"assertions": merge_repair(state.packet, redrafted, targets)})
    usage = state.llm_usage or {}
    return {
        "packet": packet,
        "draft_packet": state.draft_packet or state.packet,
        "draft_verification": state.draft_verification or state.verification,
        "repairs": state.repairs + 1,
        "route": "assemble",
        "assembled_by": f"{result.provider}:{result.model}",
        "llm_usage": {  # every call of the case, first draft and repairs together
            "input_tokens": _add(usage.get("input_tokens"), result.input_tokens),
            "output_tokens": _add(usage.get("output_tokens"), result.output_tokens),
            "fallback": bool(usage.get("fallback")) or result.fallback,
            "citations_repaired": usage.get("citations_repaired", 0) + repaired,
            "schema_retries": usage.get("schema_retries", 0) + retries,
        },
    }


def merge_repair(draft: Packet, redrafted: list[Assertion], targets: list[str]) -> list[Assertion]:
    """The draft with each target criterion's assertions replaced by the redraft's. Anything the
    redraft says about a criterion it was not asked about is ignored, and a target it leaves out
    keeps its old assertion, still flagged: a repair can fix a flag but never drop one."""
    new: dict[str, list[Assertion]] = {}
    for a in redrafted:
        if a.criterion_id in targets:
            new.setdefault(a.criterion_id, []).append(a)
    out: list[Assertion] = []
    for a in draft.assertions:
        if a.criterion_id not in new:
            out.append(a)
        elif not any(o.criterion_id == a.criterion_id for o in out):
            out += new[a.criterion_id]  # in the place the criterion had
    mentioned = {a.criterion_id for a in draft.assertions}
    for criterion_id in targets:  # criteria the draft never addressed go last, in criteria order
        if criterion_id not in mentioned:
            out += new.get(criterion_id, [])
    return out


async def _generate(llm: LLMClient, user: str, scrub: Callable[[str], str]) -> tuple[LLMResult[PacketDraft], int]:
    """The model's draft, and how many retries it took. A reply that fails validation is retried
    once, with the error appended; a second failure fails the case. Only a schema failure is
    retried, never a refusal or a PHI leak. A retried result's tokens include the failed call's."""
    try:
        return await llm.generate(SYSTEM, user, PacketDraft), 0
    except LLMSchemaError as err:
        error = scrub(str(err))[:MAX_ERROR_CHARS]
        retry = (f"{user}\n\nYour previous reply failed validation: {error}\n"
                 "Reply again, with output that matches the schema.")
        result = await llm.generate(SYSTEM, retry, PacketDraft)
        return replace(result, input_tokens=_add(result.input_tokens, err.input_tokens),
                       output_tokens=_add(result.output_tokens, err.output_tokens)), 1


def _add(a: int | None, b: int | None) -> int | None:
    """A token total, unknown if either part is."""
    return None if a is None or b is None else a + b


def render_repair(state: CaseState, criteria: Criteria, targets: list[str]) -> str:
    """The repair prompt: the same evidence rendering, for the target criteria only, each with
    what the model wrote and why verify rejected it. Everything here is already de-identified."""
    v: Verification = state.verification
    by_id = {e.criterion_id: e for e in state.evidence}
    lines = [f"Service requested: {criteria.service}"]
    if state.as_of:
        lines.append(f"As of: {state.as_of}")
    lines += ["", "An automated check of your previous assertions against the chart rejected the ones below. "
              "Write a new assertion for each criterion listed here, and for no other."]
    for criterion in criteria.criteria:
        if criterion.id not in targets:
            continue
        lines += ["", f"Criterion {criterion.id}: {' '.join(criterion.description.split())}"]
        lines += _render_evidence(by_id.get(criterion.id))
        previous = [a for a in v.assertions if a.assertion.criterion_id == criterion.id]
        if not previous:
            lines.append("  Your previous packet had no assertion for this criterion.")
        for a in previous:
            cites = ", ".join(f"{c.resource_type} {c.id}" for c in a.assertion.citations) or "none"
            lines.append(f'  You wrote ({a.assertion.kind}, citing {cites}): "{a.assertion.text}"')
            if a.supported:
                lines.append("    The check accepted this assertion.")
            lines += [f"    Rejected: {reason}" for reason in a.reasons]
    return "\n".join(lines)


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
