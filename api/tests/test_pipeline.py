import re
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.config import settings
from app.graph.build import NODES, run_pipeline
from app.graph.criteria import load_criteria
from app.graph.nodes.assemble import PacketDraft
from app.graph.state import Assertion, ResourceRef
from app.llm.client import LLMRefusal
from app.mcp.server import build_server
from tests.test_assemble import ScriptedLLM
from tests.test_extract import CRITERIA_FILE, ORDERED, _fhir_dir
from tests.test_mcp_tools import PID
from tests.test_observability import leaks
from tests.test_verify import NEW_NOTE, OLD_NOTE

AS_OF = ORDERED + timedelta(days=120)
REAL_VALUES = ("Ada", "Lovelace", PID)


@asynccontextmanager
async def pipeline(tmp_path):
    """What the runner needs: an MCP session over the test chart, and the criteria."""
    fhir = _fhir_dir(tmp_path, [OLD_NOTE, NEW_NOTE], "active", None)
    server = build_server(fhir, settings.criteria_dir)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        yield session, load_criteria(CRITERIA_FILE)


def faithful_from_prompt(prompt: str) -> PacketDraft:
    """A model that does the job right, working only from what the prompt says."""
    assertions = []
    for block in re.split(r"\n(?=Criterion )", prompt)[1:]:
        cid = re.match(r"Criterion (\w+):", block).group(1)
        lines = [ln for ln in block.splitlines() if ln.strip().startswith("- ")]
        required = re.search(r"Required status: (\w+)", block)
        if required:
            lines = [ln for ln in lines if f"status {required.group(1)}" in ln]
        refs = re.findall(r"- (\w+) (<[A-Z]+_\d+>)", "\n".join(lines))
        status = re.search(r"Duration status: (\w+)", block)
        if "No matching records" in block or (status and status.group(1) != "met") or (required and not refs):
            assertions.append(Assertion(criterion_id=cid, kind="gap", text="not established by the chart"))
        else:
            cites = [ResourceRef(resource_type=t, id=i) for t, i in refs]
            assertions.append(Assertion(criterion_id=cid, text="established by the chart", citations=cites))
    return PacketDraft(assertions=assertions)


class Refuses:
    provider = "fake"

    async def generate(self, system, user, schema):
        raise LLMRefusal("declined (category: test)")


class Breaks:
    """Fails with a message that holds a real name, as a provider or tool error might."""

    provider = "fake"

    async def generate(self, system, user, schema):
        raise RuntimeError("upstream rejected the request for Ada Lovelace")


def collect():
    events = []
    return events, events.append


# --- the happy path ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_case_runs_end_to_end_and_returns_the_state_and_the_rehydrated_packet(tmp_path):
    async with pipeline(tmp_path) as (session, criteria):
        llm = ScriptedLLM(faithful_from_prompt)
        outcome = await run_pipeline(session, llm, PID, criteria, AS_OF)

    state = outcome.state
    assert state.patient_id.startswith("<PATIENT_") and state.assembled_by == "fake:fake-1"
    assert len(state.evidence) == 5 and state.verification and not state.verification.flagged
    assert set(outcome.seconds) == set(NODES)
    # the state is what the model saw; the packet is the reviewer's, with real ids restored
    cited_in_state = {r.id for a in state.packet.assertions for r in a.citations}
    cited_real = {r.id for a in outcome.packet.assertions for r in a.citations}
    assert cited_in_state and all(i.startswith("<") for i in cited_in_state)
    assert cited_real and not any(i.startswith("<") for i in cited_real)


@pytest.mark.anyio
async def test_the_model_only_ever_sees_placeholders(tmp_path):
    """The runner puts the guard and the gateway in place itself: nothing real reaches the prompt."""
    async with pipeline(tmp_path) as (session, criteria):
        llm = ScriptedLLM(faithful_from_prompt)
        await run_pipeline(session, llm, PID, criteria, AS_OF)
    [(system, user, _)] = llm.calls
    prompt = system + user
    assert "<CONDITION_" in user
    assert not [v for v in REAL_VALUES if re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", prompt, re.I)]


@pytest.mark.anyio
async def test_two_runs_do_not_share_a_vault(tmp_path):
    async with pipeline(tmp_path) as (session, criteria):
        first = await run_pipeline(session, ScriptedLLM(faithful_from_prompt), PID, criteria, AS_OF)
        second = await run_pipeline(session, ScriptedLLM(faithful_from_prompt), PID, criteria, AS_OF)
    # each run issues its own placeholders from 1, so a placeholder means nothing outside its run
    assert first.state.patient_id == second.state.patient_id == "<PATIENT_1>"
    assert first.packet == second.packet


# --- events -----------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_events_arrive_in_order_with_each_nodes_metadata(tmp_path):
    async with pipeline(tmp_path) as (session, criteria):
        events, on_event = collect()
        await run_pipeline(session, ScriptedLLM(faithful_from_prompt), PID, criteria, AS_OF, on_event)

    assert [(e.event, e.node) for e in events] == [
        ("node_started", "extract"), ("node_finished", "extract"),
        ("node_started", "assemble"), ("node_finished", "assemble"),
        ("node_started", "verify"), ("node_finished", "verify"),
    ]
    finished = {e.node: e.data for e in events if e.event == "node_finished"}
    assert finished["extract"]["criteria"] == 5 and "tb_screening" in finished["extract"]["criteria_not_found"]
    assert finished["assemble"]["assembled_by"] == "fake:fake-1" and finished["assemble"]["input_tokens"] == 10
    assert (finished["verify"]["supported"], finished["verify"]["flagged"]) == (5, 0)
    assert all(isinstance(e["seconds"], float) for e in finished.values())
    assert all(e.data == {} for e in events if e.event == "node_started")


@pytest.mark.anyio
async def test_events_hold_no_real_value_and_no_packet_text(tmp_path):
    async with pipeline(tmp_path) as (session, criteria):
        events, on_event = collect()
        outcome = await run_pipeline(session, ScriptedLLM(faithful_from_prompt), PID, criteria, AS_OF, on_event)
    sent = [e.as_dict() for e in events]
    assert leaks(sent, vault=type("V", (), {"known": lambda self: []})(), extra=REAL_VALUES + ("c1", "m1")) == []
    blob = str(sent)
    assert not any(a.text in blob for a in outcome.packet.assertions)  # the packet never rides in an event


@pytest.mark.anyio
async def test_an_async_handler_works_too(tmp_path):
    seen = []

    async def on_event(event):
        seen.append(event.event)

    async with pipeline(tmp_path) as (session, criteria):
        await run_pipeline(session, ScriptedLLM(faithful_from_prompt), PID, criteria, AS_OF, on_event)
    assert seen.count("node_finished") == 3


# --- failures ---------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_refused_request_stops_the_run_after_a_failed_event(tmp_path):
    async with pipeline(tmp_path) as (session, criteria):
        events, on_event = collect()
        with pytest.raises(LLMRefusal):
            await run_pipeline(session, Refuses(), PID, criteria, AS_OF, on_event)

    assert [(e.event, e.node) for e in events] == [
        ("node_started", "extract"), ("node_finished", "extract"),
        ("node_started", "assemble"), ("node_failed", "assemble"),
    ]  # verify never ran
    assert events[-1].data["error_type"] == "LLMRefusal"


@pytest.mark.anyio
async def test_an_error_message_is_scrubbed_before_it_is_reported(tmp_path):
    async with pipeline(tmp_path) as (session, criteria):
        events, on_event = collect()
        with pytest.raises(RuntimeError):
            await run_pipeline(session, Breaks(), PID, criteria, AS_OF, on_event)
    failed = events[-1]
    assert failed.event == "node_failed" and failed.data["error_type"] == "RuntimeError"
    assert "Ada" not in failed.data["error"] and "Lovelace" not in failed.data["error"]
    assert "<NAME_" in failed.data["error"]  # the names became placeholders, so the cause still reads


@pytest.mark.anyio
async def test_a_patient_that_does_not_exist_fails_before_any_node_starts(tmp_path):
    async with pipeline(tmp_path) as (session, criteria):
        events, on_event = collect()
        with pytest.raises(Exception):
            await run_pipeline(session, ScriptedLLM(faithful_from_prompt), "not-a-patient", criteria, AS_OF, on_event)
    assert events == []
