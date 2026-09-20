"""extract: gather, per criterion, only the chart evidence that bears on it.

Deterministic and driven by the criteria file: each evidence spec maps to one filtered
tool call, so the full chart is never fetched. The node can reach the chart only through
the gateway it is handed; it never sees a raw MCP session.
"""

import re
from typing import Any, Protocol

from app.graph.criteria import Criteria, Criterion, EvidenceSpec
from app.graph.state import CaseState, CriterionEvidence, EvidenceItem, NoteExcerpt, ResourceRef

SEARCH_TOOLS = {
    "Condition": "search_conditions",
    "MedicationRequest": "search_medication_requests",
    "Observation": "search_observations",
}
DATE_KEYS = ("onset_date", "authored_on", "effective_date")
OBSERVATION_LIMIT = 5

# Notes are read newest first: "active disease despite therapy" is a statement about now.
# Nearly every note carries the keywords (763 of 791 sampled), so these caps, not the
# keyword match, are what keep a 357-note chart from flooding the model's context.
MAX_NOTE_READS = 30
MAX_EXCERPT_NOTES = 5
MAX_LINES_PER_NOTE = 5
LONG_LINE = 200


class ChartGateway(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


async def extract(state: CaseState, gateway: ChartGateway, criteria: Criteria) -> dict[str, Any]:
    evidence = [await _gather(c, state.patient_id, gateway) for c in criteria.criteria]
    return {"service": criteria.service, "evidence": evidence}


async def _gather(criterion: Criterion, patient_id: str, gateway: ChartGateway) -> CriterionEvidence:
    result = CriterionEvidence(
        criterion_id=criterion.id, description=criterion.description, queries=[]
    )
    for spec in criterion.evidence:
        if spec.resource == "DocumentReference":
            result.queries.append(f"DocumentReference notes mentioning: {', '.join(spec.keywords)}")
            result.excerpts += await _note_excerpts(spec, patient_id, gateway)
            continue
        args: dict[str, Any] = {"patient_id": patient_id, "code": spec.code.code}
        if spec.resource == "Observation":
            args["limit"] = OBSERVATION_LIMIT
        result.queries.append(f"{spec.resource} {spec.code.system} {spec.code.code}")
        records = await gateway.call_tool(SEARCH_TOOLS[spec.resource], args)
        result.items += [_item(record) for record in records]
    return result


def _item(record: dict[str, Any]) -> EvidenceItem:
    return EvidenceItem(
        ref=ResourceRef(resource_type=record["resource_type"], id=record["id"]),
        label=record["code"].get("display") or record["code"]["code"],
        date=next((record[k] for k in DATE_KEYS if record.get(k)), None),
        status=record.get("status") or record.get("clinical_status"),
        value=record.get("value"),
    )


async def _note_excerpts(
    spec: EvidenceSpec, patient_id: str, gateway: ChartGateway
) -> list[NoteExcerpt]:
    documents = await gateway.call_tool("list_documents", {"patient_id": patient_id})
    excerpts: list[NoteExcerpt] = []
    for meta in reversed(documents[-MAX_NOTE_READS:]):  # listed oldest first
        note = await gateway.call_tool(
            "read_document", {"patient_id": patient_id, "document_id": meta["id"]}
        )
        if lines := find_excerpt_lines(note["text"], spec.keywords):
            excerpts.append(
                NoteExcerpt(
                    ref=ResourceRef(resource_type="DocumentReference", id=meta["id"]),
                    date=meta.get("date"),
                    lines=lines,
                )
            )
            if len(excerpts) == MAX_EXCERPT_NOTES:
                break
    return excerpts[::-1]  # back to oldest first, like every other list


def find_excerpt_lines(text: str, keywords: list[str]) -> list[str]:
    """The lines (or, for long paragraphs, sentences) of a note that mention a keyword."""
    needles = [k.lower() for k in keywords]
    seen: set[str] = set()
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        pieces = re.split(r"(?<=[.;])\s+", line) if len(line) > LONG_LINE else [line]
        for piece in pieces:
            key = piece.lower()
            if piece and key not in seen and any(n in key for n in needles):
                seen.add(key)
                found.append(piece)
    return found[:MAX_LINES_PER_NOTE]
