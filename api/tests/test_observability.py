import json
import re
from datetime import timedelta
from pathlib import Path

import logfire
import pytest
from logfire.testing import CaptureLogfire

from app.config import Settings
from app.graph.nodes.assemble import PacketDraft, assemble
from app.graph.nodes.extract import extract
from app.graph.nodes.verify import verify
from app.llm.client import LLMResult
from app.llm.guard import GuardedLLM, PhiLeak
from app.observability import _allow_agent_description, record_agent_usage, setup_observability
from tests.test_extract import ORDERED
from tests.test_mcp_tools import PID
from tests.test_assemble import ScriptedLLM, faithful_draft
from tests.test_verify import case

REAL_STRINGS = (PID, "Ada", "Lovelace", "c1", "m1", "note000", "note001", "2022-01-01", "2022-01-15", "2022-02-01")


def spans_of(capfire: CaptureLogfire) -> list[dict]:
    return capfire.exporter.exported_spans_as_dict()


def attrs(span: dict) -> dict:
    """Span attributes, with JSON-encoded lists and dicts decoded."""
    out = {}
    for key, value in span["attributes"].items():
        if isinstance(value, str) and value[:1] in "[{":
            try:
                value = json.loads(value)
            except ValueError:
                pass
        out[key] = value
    return out


def leaks(spans: list[dict], vault, extra=REAL_STRINGS) -> list[str]:
    """Every real value that appears, as a whole token, anywhere in what was recorded."""
    blob = json.dumps(spans, default=str)
    candidates = {orig for _, orig in vault.known()} | set(extra)
    return sorted(
        c for c in candidates
        if len(c) >= 2 and re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", blob, re.IGNORECASE)
    )


# --- spans ------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_each_node_records_a_span_with_summary_attributes(tmp_path, capfire):
    async with case(tmp_path, mtx_status="active") as c:
        capfire.exporter.clear()  # drop the spans from building the case
        as_of = ORDERED + timedelta(days=120)
        state = c.state.model_copy(update=await extract(c.state, c.gateway, c.criteria, as_of))
        update = await assemble(state, c.criteria, ScriptedLLM(faithful_draft(c)))
        state = state.model_copy(update=update)
        await verify(state, c.gateway, c.criteria)
        spans = spans_of(capfire)

    by_name = {s["name"]: attrs(s) for s in spans}
    assert [s["name"] for s in spans] == [
        "graph.extract", "invoke_agent assemble", "graph.assemble", "graph.verify"
    ]
    ex, asm, ver = by_name["graph.extract"], by_name["graph.assemble"], by_name["graph.verify"]

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
async def test_assemble_registers_as_an_agent_and_records_no_content(tmp_path, capfire):
    async with case(tmp_path, mtx_status="active") as c:
        capfire.exporter.clear()
        await assemble(c.state, c.criteria, ScriptedLLM(faithful_draft(c)))
        [agent] = [s for s in spans_of(capfire) if s["name"] == "invoke_agent assemble"]
    a = attrs(agent)
    assert a["gen_ai.operation.name"] == "invoke_agent" and a["gen_ai.agent.name"] == "assemble"
    assert a["gen_ai.provider.name"] == "fake" and a["gen_ai.request.model"] == "fake-1"
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (10, 5)
    # metadata only: never the prompt, the response, or the system instructions
    assert not [k for k in a if k.startswith(("gen_ai.input", "gen_ai.output", "gen_ai.system_instructions"))]


def test_agent_cost_is_recorded_for_known_models_and_skipped_for_unknown_ones(capfire):
    def cost_for(provider, model):
        capfire.exporter.clear()
        with logfire.span("agent") as span:
            record_agent_usage(span, LLMResult(parsed=None, provider=provider, model=model,
                                               input_tokens=1960, output_tokens=585))
        return attrs(capfire.exporter.exported_spans_as_dict()[0]).get("operation.cost")

    assert cost_for("anthropic", "claude-haiku-4-5") == pytest.approx(0.0049, abs=0.0005)
    assert cost_for("anthropic", "claude-haiku-4-5-20251001") == cost_for("anthropic", "claude-haiku-4-5")
    assert 0 < cost_for("gemini", "gemini-3.8-flash") < 0.01
    assert cost_for("fake", "fake-1") is None  # unknown model: no cost, and no error


@pytest.mark.anyio
async def test_verify_span_names_the_flagged_criteria(tmp_path, capfire):
    async with case(tmp_path, mtx_status="completed") as c:
        capfire.exporter.clear()
        packet = faithful_draft(c).assertions  # claims dmard_trial is met, but the order is completed
        state = c.state.model_copy(update=await assemble(c.state, c.criteria, ScriptedLLM(PacketDraft(assertions=packet))))
        await verify(state, c.gateway, c.criteria)
        ver = attrs(next(s for s in spans_of(capfire) if s["name"] == "graph.verify"))
    assert ver["flagged"] == 1 and ver["flagged_criteria"] == ["dmard_trial"]


# --- the PHI boundary -------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_real_identifier_appears_in_any_recorded_span(tmp_path, capfire):
    async with case(tmp_path, mtx_status="active") as c:  # spans include building the case
        as_of = ORDERED + timedelta(days=120)
        state = c.state.model_copy(update=await extract(c.state, c.gateway, c.criteria, as_of))
        state = state.model_copy(update=await assemble(state, c.criteria, ScriptedLLM(faithful_draft(c))))
        await verify(state, c.gateway, c.criteria)
        recorded = spans_of(capfire)
        assert len(recorded) >= 3
        assert leaks(recorded, c.gateway.anonymizer.vault) == []


@pytest.mark.anyio
async def test_the_leak_check_can_actually_fail(tmp_path, capfire):
    """A control: without this the test above could pass vacuously."""
    async with case(tmp_path, mtx_status="active") as c:
        capfire.exporter.clear()
        logfire.info("deliberate leak", patient=PID, note="Ada Lovelace, seen 2022-02-01")
        found = leaks(spans_of(capfire), c.gateway.anonymizer.vault)
    assert PID in found and "Lovelace" in found and "2022-02-01" in found


@pytest.mark.anyio
async def test_a_blocked_prompt_is_recorded_as_a_failure_without_the_value(tmp_path, capfire):
    async with case(tmp_path, mtx_status="active") as c:
        vault = c.gateway.anonymizer.vault
        vault.placeholder("NAME", "Abel832")
        state = c.state.model_copy(deep=True)
        state.evidence[0].items[0].label = "Abel832 rheumatoid arthritis"  # a leak upstream of assemble
        capfire.exporter.clear()
        with pytest.raises(PhiLeak):
            await assemble(state, c.criteria, GuardedLLM(ScriptedLLM(faithful_draft(c)), vault))
        recorded = spans_of(capfire)

    [span] = [s for s in recorded if s["name"] == "graph.assemble"]
    events = [e for e in span["events"] if e["name"] == "exception"]
    assert events and events[0]["attributes"]["exception.type"].endswith("PhiLeak")
    assert leaks(recorded, vault, extra=("Abel832",)) == []


# --- Logfire's own scrubbing --------------------------------------------------------------------

def scrub_hits(spans: list[dict]) -> dict[str, set[str]]:
    """Attributes whose name or value Logfire's default secret patterns would redact."""
    from logfire._internal.scrubbing import DEFAULT_PATTERNS

    hits: dict[str, set[str]] = {}
    for span in spans:
        for key, value in span["attributes"].items():
            if key.startswith("logfire."):  # Logfire's own bookkeeping, e.g. what it scrubbed
                continue
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            for pattern in DEFAULT_PATTERNS:
                for haystack in (key, text):
                    found = re.search(pattern, haystack, re.IGNORECASE)
                    if found:
                        hits.setdefault(key, set()).add(found.group(0).lower())
    return hits


@pytest.mark.anyio
async def test_only_the_agent_description_trips_logfires_scrubbing(tmp_path, capfire):
    """Anything else that matched would show up as a redacted blank in the Logfire UI."""
    async with case(tmp_path, mtx_status="active") as c:
        state = c.state.model_copy(update=await assemble(c.state, c.criteria, ScriptedLLM(faithful_draft(c))))
        await verify(state, c.gateway, c.criteria)
        assert scrub_hits(spans_of(capfire)) == {"gen_ai.agent.description": {"auth"}}


def test_the_scrubbing_exception_is_exactly_one_attribute_and_one_match():
    def match(path, text):
        return logfire.ScrubMatch(path=path, value="v", pattern_match=re.search("auth|secret", text, re.I))

    agent = ("attributes", "gen_ai.agent.description")
    assert _allow_agent_description(match(agent, "prior authorization")) == "v"
    assert _allow_agent_description(match(agent, "the secret")) is None            # another pattern
    assert _allow_agent_description(match(("attributes", "note"), "authorization")) is None  # another attribute
    assert _allow_agent_description(match(("attributes", "auth_token"), "auth")) is None


# --- setup ------------------------------------------------------------------------------------

@pytest.fixture
def recorded_calls(monkeypatch):
    calls = {"configure": [], "instrument": []}
    monkeypatch.setattr(logfire, "configure", lambda **kw: calls["configure"].append(kw))
    monkeypatch.setattr(logfire, "instrument_anthropic", lambda *a, **k: calls["instrument"].append("anthropic"))
    monkeypatch.setattr(logfire, "instrument_google_genai", lambda *a, **k: calls["instrument"].append("gemini"))
    return calls


def test_without_a_token_nothing_is_sent_and_nothing_extra_is_instrumented(recorded_calls):
    assert setup_observability(Settings(_env_file=None, logfire_token="")) == "local"
    [cfg] = recorded_calls["configure"]
    assert cfg["send_to_logfire"] is False and cfg["token"] is None
    assert cfg["console"] is False and cfg["metrics"] is False
    assert cfg["scrubbing"].callback is _allow_agent_description
    assert recorded_calls["instrument"] == []


def test_a_token_turns_sending_on_but_still_records_metadata_only(recorded_calls):
    assert setup_observability(Settings(_env_file=None, logfire_token="secret")) == "cloud"
    [cfg] = recorded_calls["configure"]
    assert cfg["send_to_logfire"] is True and cfg["token"] == "secret"
    assert recorded_calls["instrument"] == []  # content capture is opt-in, separate from sending


def test_console_flag_shows_spans_locally(recorded_calls):
    setup_observability(Settings(_env_file=None, logfire_console=True))
    assert recorded_calls["configure"][0]["console"] is None  # None = Logfire's default console output


@pytest.mark.parametrize("provider, expected", [("anthropic", ["anthropic"]), ("gemini", ["gemini"])])
def test_content_capture_is_opt_in_and_instruments_only_the_active_provider(recorded_calls, provider, expected):
    setup_observability(Settings(_env_file=None, llm_provider=provider, logfire_capture_llm_content=True))
    assert recorded_calls["instrument"] == expected


# --- structure: what must never be instrumented -----------------------------------------------

FORBIDDEN = ("instrument_mcp", "instrument_fastapi", "instrument_httpx", "instrument_requests",
             "instrument_starlette", "instrument_asgi", "instrument_aiohttp_client", "instrument_pydantic_ai")
ONLY_IN_OBSERVABILITY = ("instrument_anthropic", "instrument_google_genai")


def test_nothing_that_sees_raw_data_is_auto_instrumented():
    app = Path(__file__).resolve().parents[1] / "app"
    for path in app.rglob("*.py"):
        text = path.read_text()
        for name in FORBIDDEN:
            assert name not in text, f"{path.name} uses {name}: it would record raw, unscrubbed data"
        if path.name != "observability.py":
            for name in ONLY_IN_OBSERVABILITY:
                assert name not in text, f"{name} belongs in observability.py, behind the opt-in flag"
