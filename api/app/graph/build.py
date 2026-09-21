"""The case graph: extract -> assemble -> verify, as a LangGraph StateGraph.

Three nodes, in a line, on purpose. The nodes stay plain async functions that take their
dependencies as arguments; this module binds those dependencies for one case and wires the
edges. The gateway and the LLM are per case (each case has its own vault), so the graph is
built per case too. Compiling is cheap.
"""

from datetime import date

from langgraph.graph import END, START, StateGraph

from app.graph.criteria import Criteria
from app.graph.nodes.assemble import assemble
from app.graph.nodes.extract import ChartGateway, extract
from app.graph.nodes.verify import verify
from app.graph.state import CaseState
from app.llm.client import LLMClient


def build_graph(gateway: ChartGateway, criteria: Criteria, llm: LLMClient, as_of: date):
    """`gateway` is the PHI gateway and `llm` is already behind the prompt guard: the graph
    adds no path to raw data or to an unguarded model."""

    async def extract_node(state: CaseState):
        return await extract(state, gateway, criteria, as_of)

    async def assemble_node(state: CaseState):
        return await assemble(state, criteria, llm)

    async def verify_node(state: CaseState):
        return await verify(state, gateway, criteria)

    graph = StateGraph(CaseState)
    graph.add_node("extract", extract_node)
    graph.add_node("assemble", assemble_node)
    graph.add_node("verify", verify_node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "assemble")
    graph.add_edge("assemble", "verify")
    graph.add_edge("verify", END)
    return graph.compile()

