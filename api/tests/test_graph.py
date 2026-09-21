from datetime import timedelta

import pytest

from app.graph.build import build_graph
from app.graph.state import CaseState
from tests.test_assemble import ScriptedLLM, faithful_draft
from tests.test_extract import ORDERED
from tests.test_observability import leaks
from tests.test_verify import case


@pytest.mark.anyio
async def test_graph_runs_extract_assemble_verify_in_a_line(tmp_path, ls):
    async with case(tmp_path, mtx_status="active") as c:
        ls.clear()
        graph = build_graph(c.gateway, c.criteria, ScriptedLLM(faithful_draft(c)), ORDERED + timedelta(days=120))
        updates = [chunk async for chunk in graph.astream(
            CaseState(patient_id=c.state.patient_id), stream_mode="updates")]
        final = CaseState.model_validate(await graph.ainvoke(CaseState(patient_id=c.state.patient_id)))

    assert [next(iter(u)) for u in updates] == ["extract", "assemble", "verify"]
    assert final.service == c.criteria.service and len(final.evidence) == 5
    assert final.assembled_by == "fake:fake-1"
    assert final.llm_usage == {"input_tokens": 10, "output_tokens": 5, "fallback": False, "citations_repaired": 0}
    assert final.verification is not None and not final.verification.flagged
    # The node runs still fire, once per node per invocation, in order.
    names = [r["name"] for r in ls.runs if r["name"].startswith("graph.")]
    assert names[:3] == ["graph.extract", "graph.assemble", "graph.verify"]


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
