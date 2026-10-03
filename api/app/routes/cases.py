"""Cases: create one for a patient, run the pipeline on it in the background, read it back.

A demo store: cases live in memory and are gone when the API restarts. What a case holds is the
reviewer's view, so it is rehydrated: real record ids and dates, and each cited record fetched
from the chart so the reviewer can see what a citation points at. The graph itself never sees
any of it; that boundary is `run_pipeline`'s, unchanged.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.graph.build import PipelineEvent, PipelineOutcome, ToolSession, run_pipeline
from app.graph.criteria import Criteria
from app.graph.state import ResourceRef
from app.llm.client import LLMClient

router = APIRouter()
MAX_RUNNING = 2  # cases run at once; the rest wait their turn


class NewCase(BaseModel):
    patient_id: str


class CaseRecord(BaseModel):
    id: str
    patient_id: str
    patient_name: str
    service: str
    status: Literal["queued", "running", "done", "failed"] = "queued"
    created_at: str
    events: list[dict[str, Any]] = []  # node events: metadata only, as run_pipeline emits them
    error: str | None = None
    result: dict[str, Any] | None = None


@dataclass
class Runtime:
    """What the routes share, opened once by the app's lifespan."""

    session: ToolSession
    llm: LLMClient | None  # None when no provider is configured; cases then fail with that reason
    llm_error: str | None
    criteria: Criteria
    as_of: date
    cases: dict[str, CaseRecord] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_RUNNING))


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


@router.get("/patients")
async def list_patients(request: Request) -> list[dict[str, Any]]:
    patients = await _tool(runtime(request).session, "list_patients", {})
    return sorted(patients, key=lambda p: p["name"])


@router.post("/cases", status_code=201)
async def create_case(body: NewCase, request: Request) -> CaseRecord:
    rt = runtime(request)
    try:
        patient = await _tool(rt.session, "get_patient", {"patient_id": body.patient_id})
    except LookupError:
        raise HTTPException(404, f"no patient {body.patient_id!r}")
    case = CaseRecord(id=uuid.uuid4().hex[:12], patient_id=body.patient_id, patient_name=patient["name"],
                      service=rt.criteria.service, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    rt.cases[case.id] = case
    task = asyncio.create_task(_run(rt, case))
    rt.tasks.add(task)
    task.add_done_callback(rt.tasks.discard)
    return case


@router.get("/cases")
async def list_cases(request: Request) -> list[CaseRecord]:
    """Newest first, without the results (fetch a case for those)."""
    cases = sorted(runtime(request).cases.values(), key=lambda c: c.created_at, reverse=True)
    return [c.model_copy(update={"result": None, "events": []}) for c in cases]


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> CaseRecord:
    case = runtime(request).cases.get(case_id)
    if case is None:
        raise HTTPException(404, f"no case {case_id!r}")
    return case


async def _run(rt: Runtime, case: CaseRecord) -> None:
    async with rt.slots:
        case.status = "running"
        if rt.llm is None:
            case.status, case.error = "failed", rt.llm_error
            return

        def record(event: PipelineEvent) -> None:
            case.events.append(event.as_dict())

        try:
            outcome = await run_pipeline(rt.session, rt.llm, case.patient_id, rt.criteria, rt.as_of, record)
            case.result = await _result(rt, case.patient_id, outcome)
            case.status = "done"
        except Exception as err:
            # run_pipeline's node_failed event carries the error text through the scrubber; use it.
            failed = next((e for e in reversed(case.events) if e["event"] == "node_failed"), None)
            detail = failed["data"]["error"] if failed else type(err).__name__
            case.status, case.error = "failed", f"{type(err).__name__}: {detail}"


async def _result(rt: Runtime, patient_id: str, outcome: PipelineOutcome) -> dict[str, Any]:
    state, verification = outcome.state, outcome.verification
    usage = state.llm_usage or {}
    cited = {c.ref for v in verification.assertions for c in v.checks if c.exists}
    records = {f"{ref.resource_type}/{ref.id}": await _record(rt.session, patient_id, ref) for ref in cited}
    first = state.draft_verification  # placeholders, so only criterion ids leave it
    return {
        "route": state.route,
        "assembled_by": state.assembled_by,
        "repairs": state.repairs,
        "repair_failed": usage.get("repair_failed"),
        "first_pass": None if first is None else {
            "flagged": [a.assertion.criterion_id for a in first.flagged], "unaddressed": first.unaddressed,
        },
        "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "schema_retries", "citations_repaired")},
        "seconds": outcome.seconds,
        "as_of": rt.as_of.isoformat(),
        "criteria": [{"id": c.id, "description": " ".join(c.description.split())} for c in rt.criteria.criteria],
        "assertions": [
            {
                "criterion_id": v.assertion.criterion_id,
                "kind": v.assertion.kind,
                "text": v.assertion.text,
                "supported": v.supported,
                "reasons": v.reasons,
                "citations": [c.model_dump() for c in v.checks]
                or [{"ref": r.model_dump(), "exists": False, "supports": False, "reason": None}
                    for r in v.assertion.citations],
            }
            for v in verification.assertions
        ],
        "unaddressed": verification.unaddressed,
        "records": {k: v for k, v in records.items() if v is not None},
    }


async def _record(session: ToolSession, patient_id: str, ref: ResourceRef) -> dict[str, Any] | None:
    """What a citation points at, summarized for display. None if it cannot be fetched."""
    try:
        if ref.resource_type == "DocumentReference":
            r = await _tool(session, "read_document", {"patient_id": patient_id, "document_id": ref.id})
        else:
            r = await _tool(session, "get_resource", {"patient_id": patient_id,
                                                      "resource_type": ref.resource_type, "resource_id": ref.id})
    except LookupError:
        return None
    code = r.get("code") or r.get("type") or {}
    value = r.get("value")
    return {
        "resource_type": ref.resource_type,
        "id": ref.id,
        "label": code.get("display") or code.get("code"),
        "date": r.get("onset_date") or r.get("authored_on") or r.get("effective_date") or r.get("date"),
        "status": r.get("clinical_status") or r.get("status"),
        "value": None if value is None else f"{value} {r.get('unit') or ''}".strip(),
        "text": r.get("text"),
    }


async def _tool(session: ToolSession, name: str, arguments: dict[str, Any]) -> Any:
    """A raw tool call (real ids in, real data out): the reviewer's side of the boundary."""
    result = await session.call_tool(name, arguments)
    if result.isError or result.structuredContent is None:
        raise LookupError(f"{name} failed")
    data = result.structuredContent
    return data["result"] if set(data) == {"result"} else data
