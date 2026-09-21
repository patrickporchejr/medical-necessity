import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda
from langsmith import trace

from app import observability
from app.config import Settings
from app.graph.nodes.assemble import PacketDraft, assemble
from app.graph.nodes.extract import extract
from app.graph.nodes.verify import verify
from app.llm.client import LLMResult
from app.llm.guard import GuardedLLM, PhiLeak
from app.observability import estimate_cost, record_agent_usage, setup_observability
from tests.test_extract import ORDERED
from tests.test_mcp_tools import PID
from tests.test_assemble import ScriptedLLM, faithful_draft
from tests.test_verify import case

REAL_STRINGS = (PID, "Ada", "Lovelace", "c1", "m1", "note000", "note001", "2022-01-01", "2022-01-15", "2022-02-01")


def leaks(runs: list[dict], vault, extra=REAL_STRINGS) -> list[str]:
    """Every real value that appears, as a whole token, anywhere in what was sent."""
    blob = json.dumps(runs, default=str)
    candidates = {orig for _, orig in vault.known()} | set(extra)
    return sorted(
        c for c in candidates
        if len(c) >= 2 and re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", blob, re.IGNORECASE)
    )


# --- runs -------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_each_node_records_a_run_with_summary_metadata(tmp_path, ls):
    async with case(tmp_path, mtx_status="active") as c:
        ls.clear()  # drop what building the case recorded
        as_of = ORDERED + timedelta(days=120)
        state = c.state.model_copy(update=await extract(c.state, c.gateway, c.criteria, as_of))
        update = await assemble(state, c.criteria, ScriptedLLM(faithful_draft(c)))
        state = state.model_copy(update=update)
        await verify(state, c.gateway, c.criteria)

    assert [r["name"] for r in ls.runs] == ["graph.extract", "graph.assemble", "assemble", "graph.verify"]
    ex, asm, ver = (ls.metadata(ls.named(n)) for n in ("graph.extract", "graph.assemble", "graph.verify"))

    assert ex["patient"] == state.patient_id and ex["criteria"] == 5
    assert ex["criteria_not_found"] == ["tb_screening", "hepatitis_b_screening"]
    assert ex["duration_status"] == {"dmard_trial": "met"}

    assert asm["assembled_by"] == "fake:fake-1"
    assert (asm["assertions"], asm["evidence_assertions"], asm["gap_assertions"]) == (5, 3, 2)
    assert (asm["input_tokens"], asm["output_tokens"], asm["fallback"]) == (10, 5, False)
    assert asm["citations_repaired"] == 0

    assert (ver["assertions"], ver["supported"], ver["flagged"]) == (5, 5, 0)
    assert ver["flagged_criteria"] == [] and ver["unaddressed"] == []


@pytest.mark.anyio
async def test_assemble_is_a_named_run_with_the_model_and_no_content_and_no_second_token_count(tmp_path, ls):
    async with case(tmp_path, mtx_status="active") as c:
        ls.clear()
        await assemble(c.state, c.criteria, ScriptedLLM(faithful_draft(c)))
        agent = ls.named("assemble")
    meta = ls.metadata(agent)
    assert meta["model"] == "fake-1" and meta["provider"] == "fake"
    # the chat model's own run reports tokens and cost; counting them here too doubled every total
    assert "usage_metadata" not in meta and agent["run_type"] == "chain"
    # metadata only: never the prompt or the response
    assert agent["inputs"] == {} and agent["outputs"] == {}


class StubRun:
    def __init__(self):
        self.meta = {}

    def add_metadata(self, meta):
        self.meta.update(meta)


def test_agent_cost_estimate_is_recorded_for_known_models_and_skipped_for_unknown_ones():
    def cost_for(provider, model):
        run = StubRun()
        record_agent_usage(run, LLMResult(parsed=None, provider=provider, model=model,
                                          input_tokens=1960, output_tokens=585))
        return run.meta.get("estimated_cost_usd")

    assert cost_for("anthropic", "claude-haiku-4-5") == pytest.approx(0.0049, abs=0.0005)
    assert cost_for("anthropic", "claude-haiku-4-5-20251001") == cost_for("anthropic", "claude-haiku-4-5")
    assert 0 < cost_for("gemini", "gemini-3.8-flash") < 0.01
    assert cost_for("fake", "fake-1") is None  # unknown model: no cost, and no error


def test_no_token_counts_means_no_estimate():
    run = StubRun()
    record_agent_usage(run, LLMResult(parsed=None, provider="anthropic", model="claude-haiku-4-5"))
    assert run.meta == {"model": "claude-haiku-4-5"}
    assert estimate_cost(LLMResult(parsed=None, provider="a", model="m")) is None


@pytest.mark.anyio
async def test_verify_run_names_the_flagged_criteria(tmp_path, ls):
    async with case(tmp_path, mtx_status="completed") as c:
        ls.clear()
        packet = faithful_draft(c).assertions  # claims dmard_trial is met, but the order is completed
        state = c.state.model_copy(update=await assemble(c.state, c.criteria, ScriptedLLM(PacketDraft(assertions=packet))))
        await verify(state, c.gateway, c.criteria)
        ver = ls.metadata(ls.named("graph.verify"))
    assert ver["flagged"] == 1 and ver["flagged_criteria"] == ["dmard_trial"]


# --- the PHI boundary -------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_real_identifier_appears_in_anything_sent(tmp_path, ls):
    async with case(tmp_path, mtx_status="active") as c:  # includes building the case
        as_of = ORDERED + timedelta(days=120)
        state = c.state.model_copy(update=await extract(c.state, c.gateway, c.criteria, as_of))
        state = state.model_copy(update=await assemble(state, c.criteria, ScriptedLLM(faithful_draft(c))))
        await verify(state, c.gateway, c.criteria)
        assert len(ls.runs) >= 3
        assert leaks(ls.runs, c.gateway.anonymizer.vault) == []


@pytest.mark.anyio
async def test_the_leak_check_can_actually_fail(tmp_path, ls):
    """A control: without this the test above could pass vacuously."""
    async with case(tmp_path, mtx_status="active") as c:
        ls.clear()
        with trace("deliberate leak", metadata={"patient": PID, "note": "Ada Lovelace, seen 2022-02-01"}):
            pass
        found = leaks(ls.runs, c.gateway.anonymizer.vault)
    assert PID in found and "Lovelace" in found and "2022-02-01" in found


@pytest.mark.anyio
async def test_a_blocked_prompt_is_recorded_as_a_failure_without_the_value(tmp_path, ls):
    async with case(tmp_path, mtx_status="active") as c:
        vault = c.gateway.anonymizer.vault
        vault.placeholder("NAME", "Abel832")
        state = c.state.model_copy(deep=True)
        state.evidence[0].items[0].label = "Abel832 rheumatoid arthritis"  # a leak upstream of assemble
        ls.clear()
        with pytest.raises(PhiLeak):
            await assemble(state, c.criteria, GuardedLLM(ScriptedLLM(faithful_draft(c)), vault))
        run = ls.named("graph.assemble")

    assert "PhiLeak" in run["error"]
    assert leaks(ls.runs, vault, extra=("Abel832",)) == []


# --- what LangChain and LangGraph would send on their own ---------------------------------------

@pytest.mark.anyio
async def test_langchain_runs_are_sent_without_their_inputs_or_outputs(ls):
    step = RunnableLambda(lambda x: {"note": "Ada Lovelace has RA", **x}, name="a-step")
    await step.ainvoke({"prompt": "Ada Lovelace, born 1950-01-01"})
    [run] = ls.runs
    assert run["name"] == "a-step" and run["inputs"] == {} and run["outputs"] == {}
    assert "Lovelace" not in json.dumps(ls.runs)


@pytest.mark.anyio
async def test_content_capture_is_opt_in(monkeypatch):
    from tests.conftest import Captured, RecordingSession

    session = RecordingSession()
    client = observability.build_client(
        Settings(_env_file=None, langsmith_api_key="k", langsmith_capture_content=True),
        session=session, auto_batch_tracing=False,
    )
    monkeypatch.setattr(observability, "_client", client)
    with observability.tracing():
        await RunnableLambda(lambda x: x, name="step").ainvoke({"prompt": "synthetic"})
    [run] = Captured(session).runs
    assert run["inputs"] and "synthetic" in json.dumps(run["inputs"])


# --- setup ------------------------------------------------------------------------------------

@pytest.fixture
def restore_client(monkeypatch):
    monkeypatch.setattr(observability, "_client", observability._client)
    monkeypatch.setattr(observability, "_project", observability._project)


def test_without_a_key_nothing_is_sent(restore_client):
    assert setup_observability(Settings(_env_file=None, langsmith_api_key="")) == "off"
    assert observability.get_client() is None


def test_a_key_turns_sending_on(restore_client):
    assert setup_observability(Settings(_env_file=None, langsmith_api_key="k", langsmith_project="p")) == "cloud"
    assert observability.get_client() is not None and observability._project == "p"


@pytest.mark.anyio
async def test_an_ambient_langsmith_setting_cannot_bypass_the_scope(monkeypatch, restore_client):
    """With no key, tracing() is explicitly off, so a LANGSMITH_TRACING left in the shell
    cannot trace whole states through a default, unfiltered client."""
    from langsmith import Client

    sent = []
    monkeypatch.setattr(Client, "create_run", lambda self, *a, **k: sent.append(k))
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ambient")
    setup_observability(Settings(_env_file=None, langsmith_api_key=""))
    with observability.tracing():
        await RunnableLambda(lambda x: x, name="step").ainvoke({"prompt": "Ada Lovelace"})
    assert sent == []


# --- structure: what must never be traced ---------------------------------------------------------

FORBIDDEN = ("wrap_openai", "wrap_anthropic", "wrap_gemini", "wrap_sdk", "LANGSMITH_TRACING",
             "LANGCHAIN_TRACING", "tracing_v2_enabled", "instrument_", "opentelemetry")


def test_nothing_that_sees_raw_data_is_auto_traced_and_langsmith_stays_in_one_module():
    app = Path(__file__).resolve().parents[1] / "app"
    for path in app.rglob("*.py"):
        text = path.read_text()
        for name in FORBIDDEN:
            if path.name == "observability.py" and name == "LANGSMITH_TRACING":
                continue  # the docstring explains why it is never set
            assert name not in text, f"{path.name} uses {name}: it would record raw, unfiltered data"
        if path.name != "observability.py":
            assert "langsmith" not in text.lower() or path.name == "config.py", (
                f"{path.name} touches LangSmith directly; tracing belongs in observability.py")


def test_no_allowlisted_key_is_also_a_graph_state_key():
    from app.graph.state import CaseState

    state_keys = set(CaseState.model_fields)
    assert not state_keys & observability.SAFE_OUTPUT_KEYS
    assert not state_keys & observability.SAFE_INPUT_KEYS
