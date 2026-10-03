from datetime import timedelta

import pytest

from app.graph.build import build_graph, route_after_extract
from app.graph.state import CaseState
from tests.test_assemble import ScriptedLLM, faithful_draft
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
    assert final.llm_usage == {"input_tokens": 10, "output_tokens": 5, "fallback": False, "citations_repaired": 0}
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
