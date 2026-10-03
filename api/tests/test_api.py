import asyncio
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

import app.main as main
from app.config import settings
from app.mcp.server import build_server
from tests.test_assemble import ScriptedLLM
from tests.test_extract import _fhir_dir
from tests.test_mcp_tools import PID
from tests.test_pipeline import AS_OF, Refuses, faithful_from_prompt
from tests.test_verify import NEW_NOTE, OLD_NOTE


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The real app, over the test chart, with a scripted model in place of a provider."""
    fhir = _fhir_dir(tmp_path, [OLD_NOTE, NEW_NOTE], "active", None)

    @asynccontextmanager
    async def connect(_url):
        server = build_server(fhir, settings.criteria_dir)
        async with create_connected_server_and_client_session(server._mcp_server) as session:
            yield session

    monkeypatch.setattr(main, "connect", connect)
    monkeypatch.setattr(main, "build_client", lambda _settings: ScriptedLLM(faithful_from_prompt))
    monkeypatch.setattr(settings, "as_of_date", AS_OF)
    with TestClient(main.app) as client:
        yield client


def wait(client, case_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        case = client.get(f"/cases/{case_id}").json()
        if case["status"] in ("done", "failed"):
            return case
        time.sleep(0.1)
    raise AssertionError(f"case {case_id} still {case['status']}")


def test_health_says_where_the_data_and_the_model_come_from(api):
    assert api.get("/").json() | {"traces": None} == {
        "status": "ok", "mcp": "in-process", "llm": settings.llm_provider, "traces": None, "as_of": AS_OF.isoformat()}


def test_a_case_runs_in_the_background_and_comes_back_for_the_reviewer(api):
    [patient] = [p for p in api.get("/patients").json() if p["id"] == PID]
    created = api.post("/cases", json={"patient_id": PID})
    assert created.status_code == 201 and created.json()["status"] in ("queued", "running")
    assert created.json()["patient_name"] == patient["name"]

    case = wait(api, created.json()["id"])
    assert case["status"] == "done" and case["error"] is None
    assert [(e["event"], e["node"]) for e in case["events"] if e["event"] == "node_finished"] == [
        ("node_finished", "extract"), ("node_finished", "assemble"), ("node_finished", "verify")]
    result = case["result"]
    assert result["route"] == "assemble" and result["repairs"] == 0 and result["first_pass"] is None
    assert {a["criterion_id"] for a in result["assertions"]} == {c["id"] for c in result["criteria"]}
    assert all(a["supported"] for a in result["assertions"]) and result["unaddressed"] == []
    # the reviewer sees real ids, and every cited record is there to look at
    cited = [c["ref"] for a in result["assertions"] for c in a["citations"]]
    assert cited and not any(r["id"].startswith("<") for r in cited)
    assert {f"{r['resource_type']}/{r['id']}" for r in cited} <= set(result["records"])
    note = next(r for r in result["records"].values() if r["resource_type"] == "DocumentReference")
    assert note["text"] and note["date"]

    listed = api.get("/cases").json()
    assert [c["id"] for c in listed] == [case["id"]] and listed[0]["result"] is None


def test_a_failed_case_reports_why_and_an_unknown_patient_is_a_404(api):
    api.app.state.runtime.llm = Refuses()
    case = wait(api, api.post("/cases", json={"patient_id": PID}).json()["id"])
    assert case["status"] == "failed" and case["error"].startswith("LLMRefusal")
    assert api.post("/cases", json={"patient_id": "nobody"}).status_code == 404
    assert api.get("/cases/nope").status_code == 404


@pytest.mark.anyio
async def test_the_mcp_connection_reopens_a_dropped_session_and_retries_the_call(monkeypatch):
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    from app.mcp.client import McpConnection

    monkeypatch.setattr(McpConnection, "RECONNECT_SECONDS", 0)
    opened = []

    class Session:
        def __init__(self, n):
            self.n = n

        async def call_tool(self, name, arguments):
            if self.n == 1:  # the first session dies, as when the mcp container restarts
                raise McpError(ErrorData(code=-32600, message="Session terminated"))
            return f"{name} on session {self.n}"

    @asynccontextmanager
    async def open_session():
        opened.append(len(opened) + 1)
        yield Session(opened[-1])

    conn = McpConnection(open_session)
    supervisor = asyncio.create_task(conn.run())
    try:
        assert await conn.call_tool("list_patients", {}) == "list_patients on session 2"
        assert opened == [1, 2]
    finally:
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)
