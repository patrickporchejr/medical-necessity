import ast
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.graph.nodes.assemble import PacketDraft, assemble, render_evidence, repair_citations
from app.graph.nodes.verify import verify
from app.graph.state import Assertion, ResourceRef
from app.llm.anthropic_client import FALLBACK_BETA, AnthropicClient
from app.llm.client import LLMError, LLMRefusal, LLMResult
from app.llm.gemini_client import GeminiClient
from app.llm.guard import GuardedLLM, PhiLeak, assert_clean
from app.llm.prompts.assemble import SYSTEM
from app.phi.vault import Vault
from tests.test_mcp_tools import PID
from tests.test_verify import Case, case


class ScriptedLLM:
    """Stands in for a provider: returns a prepared draft and records what it was sent."""

    provider = "fake"

    def __init__(self, draft):
        self.draft, self.calls = draft, []

    async def generate(self, system, user, schema):
        self.calls.append((system, user, schema))
        draft = self.draft(user) if callable(self.draft) else self.draft
        return LLMResult(parsed=draft, provider="fake", model="fake-1", input_tokens=10, output_tokens=5)


def faithful_draft(c: Case) -> PacketDraft:
    return PacketDraft(assertions=c.faithful())


# --- the prompt -------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_prompt_carries_the_evidence_and_the_duration_facts(tmp_path):
    async with case(tmp_path, mtx_status="active", days=120) as c:
        prompt = render_evidence(c.state, c.criteria)
    ids = [i.ref.id for e in c.state.evidence for i in e.items]
    assert all(i in prompt for i in ids) and "<DOCUMENTREFERENCE_" in prompt
    assert "Criterion dmard_trial" in prompt and "in effect for 120 days" in prompt
    assert "Minimum duration: 90 days. Duration status: met." in prompt
    assert "Criterion tb_screening" in prompt and "No matching records were found" in prompt
    assert f"As of: {c.state.as_of}" in prompt


@pytest.mark.anyio
async def test_a_resolved_diagnosis_is_shown_with_its_status_and_the_requirement(tmp_path):
    async with case(tmp_path, ra_status="resolved") as c:
        prompt = render_evidence(c.state, c.criteria)
    block = prompt.split("Criterion ra_diagnosis")[1].split("Criterion dmard_trial")[0]
    assert "status resolved" in block and "Required status: active." in block
    assert "No matching records" not in block  # the record exists; it just does not qualify


@pytest.mark.anyio
async def test_day_counts_appear_only_on_criteria_with_a_duration(tmp_path):
    async with case(tmp_path, mtx_status="active", days=120) as c:
        ra = next(e for e in c.state.evidence if e.criterion_id == "ra_diagnosis")
    assert ra.items[0].status == "active" and ra.items[0].days_in_effect is None


@pytest.mark.anyio
async def test_prompt_holds_no_real_identifier_and_passes_the_guard(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        prompt = render_evidence(c.state, c.criteria)
        assert_clean(prompt, c.gateway.anonymizer.vault)
    for real in (PID, "Ada", "Lovelace", "c1", "note000", "note001", "2022-01-15", "2022-02-01"):
        # whole-token match: "Ada" must not be found inside "Adalimumab"
        assert not re.search(rf"(?<!\w){re.escape(real)}(?!\w)", prompt), real


def test_system_prompt_does_not_leak_the_verdict_it_leaves_to_verify():
    assert "established" not in SYSTEM.lower()


# --- the node ---------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_assemble_builds_the_packet_and_records_who_wrote_it(tmp_path):
    async with case(tmp_path) as c:
        llm = ScriptedLLM(faithful_draft(c))
        update = await assemble(c.state, c.criteria, llm)
    [(system, user, schema)] = llm.calls
    assert system == SYSTEM and schema is PacketDraft and "Criterion ra_diagnosis" in user
    assert update["packet"].service == c.state.service
    assert len(update["packet"].assertions) == 5
    assert update["assembled_by"] == "fake:fake-1"


@pytest.mark.anyio
async def test_a_faithful_draft_passes_verify(tmp_path):
    async with case(tmp_path) as c:
        update = await assemble(c.state, c.criteria, ScriptedLLM(faithful_draft(c)))
        state = c.state.model_copy(update=update)
        v = (await verify(state, c.gateway, c.criteria))["verification"]
    assert v.flagged == [] and v.unaddressed == []


@pytest.mark.anyio
async def test_an_overclaiming_draft_reaches_verify_and_is_flagged(tmp_path):
    """assemble does not filter the model: the independent check is verify's job."""
    async with case(tmp_path, mtx_status="completed") as c:
        bad = faithful_draft(c).assertions
        bad[1] = Assertion(criterion_id="dmard_trial", text="3 months of MTX", citations=[c.ev["dmard_trial"].items[0].ref])
        bad[3] = Assertion(
            criterion_id="tb_screening", text="TB negative",
            citations=[ResourceRef(resource_type="Observation", id="<OBSERVATION_99>")],
        )
        update = await assemble(c.state, c.criteria, ScriptedLLM(PacketDraft(assertions=bad)))
        v = (await verify(c.state.model_copy(update=update), c.gateway, c.criteria))["verification"]
    flagged = {a.assertion.criterion_id for a in v.flagged}
    assert flagged == {"dmard_trial", "tb_screening"}


# --- ids the model wrote without their angle brackets ------------------------------------------

def unbracketed(draft: PacketDraft) -> PacketDraft:
    return PacketDraft(assertions=[
        a.model_copy(update={"citations": [
            ResourceRef(resource_type=r.resource_type, id=r.id.strip("<>")) for r in a.citations
        ]})
        for a in draft.assertions
    ])


def test_repair_restores_only_bracket_variants_of_a_placeholder():
    def ids(*given):
        [a], n = repair_citations([Assertion(criterion_id="c", text="t", citations=[
            ResourceRef(resource_type="Condition", id=i) for i in given])])
        return [r.id for r in a.citations], n

    assert ids("CONDITION_1", "<CONDITION_2", "CONDITION_3>", "  CONDITION_4 ") == (
        ["<CONDITION_1>", "<CONDITION_2>", "<CONDITION_3>", "<CONDITION_4>"], 4)
    assert ids("<CONDITION_1>") == (["<CONDITION_1>"], 0)          # already right: not counted
    # not placeholders even with brackets: left exactly as written, for verify to flag
    assert ids("banana", "condition_1", "CONDITION_", "<CONDITION_1> extra") == (
        ["banana", "condition_1", "CONDITION_", "<CONDITION_1> extra"], 0)


@pytest.mark.anyio
async def test_bracketless_ids_are_restored_counted_and_then_verify_cleanly(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        cited = sum(len(a.citations) for a in faithful_draft(c).assertions)
        update = await assemble(c.state, c.criteria, ScriptedLLM(unbracketed(faithful_draft(c))))
        assert update["llm_usage"]["citations_repaired"] == cited > 0
        assert all(r.id.startswith("<") for a in update["packet"].assertions for r in a.citations)
        v = (await verify(c.state.model_copy(update=update), c.gateway, c.criteria))["verification"]
    assert v.flagged == []


@pytest.mark.anyio
async def test_a_fabricated_id_is_not_mistaken_for_a_formatting_slip(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        draft = faithful_draft(c).assertions
        draft[0] = draft[0].model_copy(update={"citations": [ResourceRef(resource_type="Condition", id="CONDITION_99")]})
        update = await assemble(c.state, c.criteria, ScriptedLLM(PacketDraft(assertions=draft)))
        v = (await verify(c.state.model_copy(update=update), c.gateway, c.criteria))["verification"]
    assert update["packet"].assertions[0].citations[0].id == "<CONDITION_99>"  # shaped right, still invented
    assert [a.assertion.criterion_id for a in v.flagged] == ["ra_diagnosis"]
    assert not v.flagged[0].checks[0].exists


def test_the_system_prompt_puts_brackets_on_record_ids_and_not_on_criterion_ids():
    # A model given "ids have brackets" wrapped the criterion ids too (<ra_diagnosis>); keep the
    # two kinds of id explicitly apart.
    assert "brackets and all" in SYSTEM and "the angle brackets are part of the id" in SYSTEM
    assert "never wrapped in brackets" in SYSTEM


# --- the guard --------------------------------------------------------------------------------

def test_guard_blocks_real_values_in_any_case_and_never_echoes_them():
    vault = Vault()
    vault.placeholder("NAME", "Abel832")
    vault.placeholder("CONDITION", "9f3c2a77-real-id")
    for text in ("Patient Abel832 has RA", "patient ABEL832.", "cite 9f3c2a77-real-id please"):
        with pytest.raises(PhiLeak) as err:
            assert_clean(text, vault)
        assert "Abel832" not in str(err.value) and "9f3c2a77" not in str(err.value)
    assert_clean("Patient <NAME_1> has <CONDITION_1>; Abel8320 is unrelated", vault)


@pytest.mark.anyio
async def test_guarded_client_stops_a_leaking_prompt_before_the_provider_sees_it():
    vault = Vault()
    vault.placeholder("NAME", "Abel832")
    inner = ScriptedLLM(PacketDraft(assertions=[]))
    llm = GuardedLLM(inner, vault)
    with pytest.raises(PhiLeak):
        await llm.generate("sys", "Abel832 has RA", PacketDraft)
    with pytest.raises(PhiLeak):
        await llm.generate("Abel832", "clean", PacketDraft)
    assert inner.calls == []
    await llm.generate("sys", "<NAME_1> has RA", PacketDraft)
    assert len(inner.calls) == 1


@pytest.mark.anyio
async def test_the_real_pipeline_prompt_survives_the_guard(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        llm = GuardedLLM(ScriptedLLM(faithful_draft(c)), c.gateway.anonymizer.vault)
        update = await assemble(c.state, c.criteria, llm)
    assert update["packet"].assertions


# --- provider clients, against fakes (no keys, no network) ------------------------------------

DRAFT = PacketDraft(assertions=[Assertion(criterion_id="ra_diagnosis", kind="gap", text="none")])


def anthropic_response(**over):
    base = dict(
        stop_reason="end_turn", stop_details=None, parsed_output=DRAFT, model="claude-haiku-4-5",
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, iterations=[]),
    )
    return SimpleNamespace(**(base | over))


class FakeAnthropic:
    def __init__(self, response):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))
        self._response = response

    async def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


@pytest.mark.anyio
async def test_anthropic_client_defaults_to_haiku_and_sends_only_what_it_supports():
    fake = FakeAnthropic(anthropic_response())
    result = await AnthropicClient(client=fake).generate("sys", "user", PacketDraft)
    [sent] = fake.calls
    assert sent["model"] == "claude-haiku-4-5" and sent["system"] == "sys"
    assert sent["messages"] == [{"role": "user", "content": "user"}]
    assert sent["output_format"] is PacketDraft
    assert "output_config" not in sent and "betas" not in sent and "fallbacks" not in sent
    assert (result.parsed, result.provider, result.model) == (DRAFT, "anthropic", "claude-haiku-4-5")
    assert (result.input_tokens, result.output_tokens, result.fallback) == (100, 20, False)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model, effort, fallbacks",
    [("claude-opus-5", True, True), ("claude-sonnet-5", True, False), ("claude-haiku-4-5", False, False)],
)
async def test_anthropic_client_only_sends_what_the_model_supports(model, effort, fallbacks):
    fake = FakeAnthropic(anthropic_response(model=model))
    await AnthropicClient(model=model, client=fake).generate("s", "u", PacketDraft)
    [sent] = fake.calls
    assert ("output_config" in sent) is effort
    assert ("fallbacks" in sent and "betas" in sent) is fallbacks
    assert sent["output_format"] is PacketDraft and sent["model"] == model


@pytest.mark.anyio
async def test_anthropic_client_reports_when_a_fallback_model_answered():
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, iterations=[SimpleNamespace(type="fallback_message")])
    fake = FakeAnthropic(anthropic_response(usage=usage, model="claude-opus-4-8"))
    result = await AnthropicClient(client=fake).generate("s", "u", PacketDraft)
    assert result.fallback and result.model == "claude-opus-4-8"


@pytest.mark.anyio
async def test_anthropic_refusal_and_empty_output_are_errors_not_empty_packets():
    refused = anthropic_response(stop_reason="refusal", stop_details=SimpleNamespace(category="bio"), parsed_output=None)
    with pytest.raises(LLMRefusal, match="bio"):
        await AnthropicClient(client=FakeAnthropic(refused)).generate("s", "u", PacketDraft)
    with pytest.raises(LLMError):
        await AnthropicClient(client=FakeAnthropic(anthropic_response(parsed_output=None))).generate("s", "u", PacketDraft)


class FakeGemini:
    def __init__(self, text, blocked=None, thoughts=None):
        self.calls = []
        response = SimpleNamespace(
            text=text, model_version="gemini-3.8-flash-001",
            usage_metadata=SimpleNamespace(prompt_token_count=80, candidates_token_count=15,
                                           thoughts_token_count=thoughts),
            prompt_feedback=SimpleNamespace(block_reason=blocked),
        )

        async def generate_content(**kwargs):
            self.calls.append(kwargs)
            return response

        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


@pytest.mark.anyio
async def test_gemini_client_sends_the_schema_and_validates_the_reply():
    fake = FakeGemini(DRAFT.model_dump_json())
    result = await GeminiClient(client=fake).generate("sys", "user", PacketDraft)
    [sent] = fake.calls
    cfg = sent["config"]
    assert sent["model"] == "gemini-3.8-flash" and sent["contents"] == "user"
    assert cfg.system_instruction == "sys" and cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == PacketDraft.model_json_schema()
    assert (result.parsed, result.provider, result.model) == (DRAFT, "gemini", "gemini-3.8-flash-001")
    assert (result.input_tokens, result.output_tokens) == (80, 15)


@pytest.mark.anyio
async def test_gemini_output_tokens_include_thinking_because_they_are_billed_as_output():
    # Measured on a real Flash call: 330 answer tokens and 1229 thinking tokens.
    result = await GeminiClient(client=FakeGemini(DRAFT.model_dump_json(), thoughts=1229)).generate("s", "u", PacketDraft)
    assert result.output_tokens == 15 + 1229
    quiet = await GeminiClient(client=FakeGemini(DRAFT.model_dump_json(), thoughts=None)).generate("s", "u", PacketDraft)
    assert quiet.output_tokens == 15  # a model that reports no thinking tokens


@pytest.mark.anyio
async def test_gemini_blocked_or_malformed_replies_are_errors():
    with pytest.raises(LLMRefusal, match="SAFETY"):
        await GeminiClient(client=FakeGemini(None, blocked="SAFETY")).generate("s", "u", PacketDraft)
    with pytest.raises(LLMError, match="does not match"):
        await GeminiClient(client=FakeGemini('{"assertions": "nope"}')).generate("s", "u", PacketDraft)


def test_the_schema_the_models_are_given_is_the_packet_contract():
    props = PacketDraft.model_json_schema()["$defs"]["Assertion"]["properties"]
    assert set(props) == {"criterion_id", "kind", "text", "citations"}
    assert props["kind"]["enum"] == ["evidence", "gap"]


# --- structure --------------------------------------------------------------------------------

def test_graph_and_the_neutral_llm_modules_import_no_provider_sdk():
    app = Path(__file__).resolve().parents[1] / "app"
    files = [*app.joinpath("graph").rglob("*.py"), app / "llm" / "client.py", app / "llm" / "guard.py",
             *app.joinpath("llm", "prompts").rglob("*.py")]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert name.split(".")[0] not in {"anthropic", "google", "openai"}, (path, name)


# --- live smoke tests: opt-in, need real keys -------------------------------------------------

def _live(provider):
    from app.config import settings
    key = settings.anthropic_api_key if provider == "anthropic" else settings.gemini_api_key
    return pytest.mark.skipif(not key, reason=f"no {provider} key configured")


@_live("anthropic")
@pytest.mark.anyio
async def test_live_anthropic_returns_a_valid_draft():
    from app.config import settings
    result = await AnthropicClient(model=settings.anthropic_model, api_key=settings.anthropic_api_key).generate(
        SYSTEM, "Criterion ra_diagnosis: RA.\n  No matching records were found in the chart.", PacketDraft
    )
    assert result.parsed.assertions and result.output_tokens


@_live("gemini")
@pytest.mark.anyio
async def test_live_gemini_returns_a_valid_draft():
    from app.config import settings
    result = await GeminiClient(model=settings.gemini_model, api_key=settings.gemini_api_key).generate(
        SYSTEM, "Criterion ra_diagnosis: RA.\n  No matching records were found in the chart.", PacketDraft
    )
    assert result.parsed.assertions and result.output_tokens
