from datetime import timedelta

import pytest

from app.graph.build import build_graph, route_after_extract
from app.graph.nodes.assemble import PacketDraft, merge_repair
from app.graph.state import Assertion, CaseState, Packet, ResourceRef
from tests.test_assemble import INVALID, FlakyLLM, ScriptedLLM, faithful_draft
from tests.test_extract import ORDERED
from tests.test_observability import leaks
from tests.test_verify import case


@pytest.mark.anyio
async def test_an_active_diagnosis_goes_through_the_model(tmp_path, ls):
    async with case(tmp_path, mtx_status="active") as c:
        ls.clear()
        graph = build_graph(c.gateway, c.criteria, ScriptedLLM(faithful_draft(c)), ORDERED + timedelta(days=120))
        updates = [chunk async for chunk in graph.astream(
            CaseState(patient_id=c.state.patient_id), stream_mode="updates")]
        final = CaseState.model_validate(await graph.ainvoke(CaseState(patient_id=c.state.patient_id)))

    assert [next(iter(u)) for u in updates] == ["extract", "assemble", "verify"]
    assert final.route == "assemble"
    assert final.service == c.criteria.service and len(final.evidence) == 5
    assert final.assembled_by == "fake:fake-1"
    assert final.llm_usage == {"input_tokens": 10, "output_tokens": 5, "fallback": False, "citations_repaired": 0,
                               "schema_retries": 0}
    assert final.verification is not None and not final.verification.flagged
    # The node runs still fire, once per node per invocation, in order.
    names = [r["name"] for r in ls.runs if r["name"].startswith("graph.")]
    assert names[:3] == ["graph.extract", "graph.assemble", "graph.verify"]
    assert ls.metadata(ls.runs[[r["name"] for r in ls.runs].index("graph.assemble")])["route"] == "assemble"


@pytest.mark.anyio
async def test_a_resolved_diagnosis_skips_the_model_and_the_packet_still_verifies(tmp_path, ls):
    """The chart has methotrexate long enough and active-disease notes, but the RA diagnosis is
    resolved. No packet can be approved, so code writes it: a gap for the diagnosis, and the
    criteria the chart does meet stated as evidence, because a gap claim there would be false."""
    async with case(tmp_path, mtx_status="active", ra_status="resolved") as c:
        ls.clear()
        llm = ScriptedLLM(faithful_draft(c))
        graph = build_graph(c.gateway, c.criteria, llm, ORDERED + timedelta(days=120))
        updates = [chunk async for chunk in graph.astream(
            CaseState(patient_id=c.state.patient_id), stream_mode="updates")]
        final = CaseState.model_validate(await graph.ainvoke(CaseState(patient_id=c.state.patient_id)))

    assert [next(iter(u)) for u in updates] == ["extract", "gap_packet", "verify"]
    assert llm.calls == [] and final.assembled_by is None and final.llm_usage is None
    assert final.route == "gap_packet"
    kinds = {a.criterion_id: a.kind for a in final.packet.assertions}
    assert kinds == {"ra_diagnosis": "gap", "dmard_trial": "evidence", "active_disease": "evidence",
                     "tb_screening": "gap", "hepatitis_b_screening": "gap"}
    assert final.verification is not None and not final.verification.flagged and not final.verification.unaddressed
    run = ls.runs[[r["name"] for r in ls.runs].index("graph.gap_packet")]
    assert ls.metadata(run)["route"] == "gap_packet" and ls.metadata(run)["gap_assertions"] == 3
    assert "graph.assemble" not in {r["name"] for r in ls.runs}


@pytest.mark.anyio
async def test_the_route_reads_the_gate_criterion_only(tmp_path):
    async with case(tmp_path, mtx_status="absent", ra_status="resolved") as c:
        assert route_after_extract(c.state) == "gap_packet"
        # without the gate criterion in the evidence, there is nothing to skip on
        no_gate = c.state.model_copy(update={"evidence": [e for e in c.state.evidence if e.criterion_id != "ra_diagnosis"]})
        assert route_after_extract(no_gate) == "assemble"
    (tmp_path / "active").mkdir()
    async with case(tmp_path / "active", mtx_status="absent") as c:
        assert route_after_extract(c.state) == "assemble"  # nothing else met, but the model still drafts


@pytest.mark.anyio
async def test_the_graphs_own_runs_are_sent_without_state_and_without_identifiers(tmp_path, ls):
    """LangGraph traces its runs itself. Every one of them, and the whole state that flowed
    through, must reach LangSmith with inputs and outputs dropped."""
    async with case(tmp_path, mtx_status="active") as c:
        ls.clear()
        graph = build_graph(c.gateway, c.criteria, ScriptedLLM(faithful_draft(c)), ORDERED + timedelta(days=120))
        await graph.ainvoke(CaseState(patient_id=c.state.patient_id))
        assert {"LangGraph", "extract", "assemble", "verify"} <= {r["name"] for r in ls.runs}
        assert [(r["name"], r["inputs"], r["outputs"]) for r in ls.runs if r["inputs"] or r["outputs"]] == []
        assert leaks(ls.runs, c.gateway.anonymizer.vault) == []


@pytest.mark.anyio
async def test_graph_flags_an_unsupported_claim_instead_of_dropping_it(tmp_path):
    async with case(tmp_path, mtx_status="stopped") as c:
        graph = build_graph(c.gateway, c.criteria, ScriptedLLM(faithful_draft(c)), ORDERED + timedelta(days=120))
        final = CaseState.model_validate(await graph.ainvoke(CaseState(patient_id=c.state.patient_id)))
    assert len(final.verification.assertions) == 5 and final.verification.flagged


# --- the repair loop --------------------------------------------------------------------------

class SequenceLLM(ScriptedLLM):
    """Answers with each draft in turn, then keeps repeating the last."""

    def __init__(self, *drafts):
        super().__init__(None)
        self.drafts = list(drafts)

    async def generate(self, system, user, schema):
        self.draft = self.drafts[min(len(self.calls), len(self.drafts) - 1)]
        return await super().generate(system, user, schema)


def overclaiming_draft(c) -> PacketDraft:
    """Right on four criteria; claims TB screening on the strength of the RA diagnosis."""
    ra = c.ev["ra_diagnosis"].items[0].ref
    return PacketDraft(assertions=[
        a if a.criterion_id != "tb_screening" else Assertion(criterion_id="tb_screening", text="screened", citations=[ra])
        for a in c.faithful()
    ])


async def run_graph(c, llm, max_repairs=1) -> CaseState:
    graph = build_graph(c.gateway, c.criteria, llm, ORDERED + timedelta(days=120), max_repairs)
    return CaseState.model_validate(await graph.ainvoke(CaseState(patient_id=c.state.patient_id)))


@pytest.mark.anyio
async def test_a_flagged_packet_is_repaired_once_and_the_first_draft_is_kept(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        llm = SequenceLLM(overclaiming_draft(c), faithful_draft(c))
        final = await run_graph(c, llm)

    assert len(llm.calls) == 2 and final.repairs == 1
    assert final.verification is not None and not final.verification.flagged and not final.verification.unaddressed
    # the first pass is kept, flags and all, so evals can still score the model on its own
    assert [a.assertion.criterion_id for a in final.draft_verification.flagged] == ["tb_screening"]
    assert final.draft_packet.assertions == overclaiming_draft(c).assertions
    # the repair prompt holds only the flagged criterion, what was written for it, and why it failed
    repair = llm.calls[1][1]
    assert "Criterion tb_screening" in repair and "Criterion ra_diagnosis" not in repair
    assert 'You wrote (evidence, citing Condition' in repair and "Rejected: " in repair
    # both calls are counted
    assert final.llm_usage["input_tokens"] == 20 and final.llm_usage["output_tokens"] == 10


@pytest.mark.anyio
async def test_a_model_that_keeps_overclaiming_stops_at_the_cap_with_its_flags_kept(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        llm = SequenceLLM(overclaiming_draft(c))
        final = await run_graph(c, llm)
        twice = SequenceLLM(overclaiming_draft(c))
        capped_at_two = await run_graph(c, twice, max_repairs=2)

    assert len(llm.calls) == 2 and final.repairs == 1
    assert [a.assertion.criterion_id for a in final.verification.flagged] == ["tb_screening"]
    assert len(final.verification.assertions) == 5  # flagged, not dropped
    assert len(twice.calls) == 3 and capped_at_two.repairs == 2


@pytest.mark.anyio
async def test_a_faithful_first_pass_makes_no_second_call(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        llm = ScriptedLLM(faithful_draft(c))
        final = await run_graph(c, llm)
    assert len(llm.calls) == 1 and final.repairs == 0
    assert final.draft_packet is None and final.draft_verification is None


@pytest.mark.anyio
async def test_an_unaddressed_criterion_is_repaired_too(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        missing = PacketDraft(assertions=[a for a in c.faithful() if a.criterion_id != "hepatitis_b_screening"])
        llm = SequenceLLM(missing, faithful_draft(c))
        final = await run_graph(c, llm)
    assert final.draft_verification.unaddressed == ["hepatitis_b_screening"]
    assert "Criterion hepatitis_b_screening" in llm.calls[1][1] and "no assertion for this criterion" in llm.calls[1][1]
    assert not final.verification.unaddressed and not final.verification.flagged
    assert [a.criterion_id for a in final.packet.assertions][-1] == "hepatitis_b_screening"


@pytest.mark.anyio
async def test_zero_repairs_turns_the_loop_off(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        llm = SequenceLLM(overclaiming_draft(c), faithful_draft(c))
        final = await run_graph(c, llm, max_repairs=0)
    assert len(llm.calls) == 1 and final.verification.flagged and final.draft_packet is None


def test_a_repair_replaces_only_the_criteria_it_was_asked_about():
    ref = ResourceRef(resource_type="Condition", id="<CONDITION_1>")
    draft = Packet(service="s", assertions=[
        Assertion(criterion_id="a", text="old a", citations=[ref]),
        Assertion(criterion_id="b", text="old b", citations=[ref]),
        Assertion(criterion_id="c", text="old c", citations=[ref]),
    ])
    redraft = [
        Assertion(criterion_id="a", kind="gap", text="new a"),  # not a target: ignored
        Assertion(criterion_id="b", kind="gap", text="new b"),
        Assertion(criterion_id="d", kind="gap", text="new d"),  # was unaddressed
    ]
    merged = merge_repair(draft, redraft, ["b", "c", "d"])
    # c was a target the redraft left out: it keeps its old, still flagged assertion
    assert [(a.criterion_id, a.text) for a in merged] == [("a", "old a"), ("b", "new b"), ("c", "old c"), ("d", "new d")]


@pytest.mark.anyio
async def test_a_repair_reply_that_fails_validation_is_retried_too(tmp_path):
    async with case(tmp_path, mtx_status="active") as c:
        llm = FlakyLLM(overclaiming_draft(c), INVALID, faithful_draft(c))
        final = await run_graph(c, llm)
    repair, retry = llm.calls[1][1], llm.calls[2][1]
    assert retry.startswith(repair) and "Your previous reply failed validation: " in retry
    assert final.repairs == 1 and not final.verification.flagged
    # every call is counted: draft 10+5, failed repair 7+3, retried repair 10+5
    usage = final.llm_usage
    assert (usage["schema_retries"], usage["input_tokens"], usage["output_tokens"]) == (1, 27, 13)
