"""The case graph: extract -> assemble -> verify, as a LangGraph StateGraph, and the runner
that puts one real patient through it.

Three nodes, in a line, on purpose. The nodes stay plain async functions that take their
dependencies as arguments; `build_graph` binds those dependencies for one case and wires the
edges. The gateway and the LLM are per case (each case has its own vault), so the graph is
built per case too. Compiling is cheap.

`run_pipeline` is the one place a case is run, for the API and the evals alike. It owns the
per-run vault, gateway and prompt guard, and reports progress as events that carry metadata
only. The rehydrated packet is in its return value and nowhere else.
"""

import inspect
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Literal, Protocol

from langgraph.graph import END, START, StateGraph

from app.graph.criteria import Criteria
from app.graph.nodes.assemble import assemble
from app.graph.nodes.extract import ChartGateway, extract
from app.graph.nodes.verify import verify
from app.graph.state import CaseState, Packet, Verification
from app.llm.client import LLMClient
from app.llm.guard import GuardedLLM
from app.observability import tracing
from app.phi.anonymize import Anonymizer
from app.phi.gateway import PhiGateway
from app.phi.rehydrate import rehydrate
from app.phi.vault import Vault

NODES = ("extract", "assemble", "verify")  # the order the graph runs them


class ToolSession(Protocol):
    """What the runner needs of an MCP client session. Graph code never imports the MCP
    client itself: whoever calls the runner holds the session."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


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
    for name, node in zip(NODES, (extract_node, assemble_node, verify_node)):
        graph.add_node(name, node)
    graph.add_edge(START, NODES[0])
    for before, after in zip(NODES, NODES[1:]):
        graph.add_edge(before, after)
    graph.add_edge(NODES[-1], END)
    return graph.compile()


@dataclass(frozen=True)
class PipelineEvent:
    """Progress of one run. `data` is metadata only: the same counts, criterion ids, model and
    token figures the node's traced run carries, plus timing. No prompt, no packet text."""

    event: Literal["node_started", "node_finished", "node_failed"]
    node: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineOutcome:
    state: CaseState  # de-identified: what the model saw and wrote
    packet: Packet  # the same packet with real ids and dates restored. For the reviewer only:
    verification: Verification  # ...and the verdicts, whose ids and reasons have placeholders too.
    # Neither may go into an event, a log or a trace.
    seconds: dict[str, float]  # per node


EventHandler = Callable[[PipelineEvent], Awaitable[None] | None]
NODE_FUNCTIONS = {"extract": extract, "assemble": assemble, "verify": verify}


async def run_pipeline(
    session: ToolSession,
    llm: LLMClient,
    patient_id: str,
    criteria: Criteria,
    as_of: date,
    on_event: EventHandler | None = None,
) -> PipelineOutcome:
    """One real patient through the graph. `patient_id` is the real id and `llm` is the raw
    client: the vault, the gateway and the prompt guard are made here, per run, so nothing
    from one case can reach another, and the vault is cleared when the run ends, however it ends. If a node fails the exception propagates, after a
    `node_failed` event whose error text has been through the scrubber."""

    async def emit(event: str, node: str, data: dict[str, Any] | None = None) -> None:
        if on_event is not None:
            handled = on_event(PipelineEvent(event, node, data or {}))
            if inspect.isawaitable(handled):
                await handled

    vault = Vault()
    gateway = PhiGateway(session, Anonymizer(vault))
    seconds: dict[str, float] = {}
    current = NODES[0]
    try:
        with tracing():
            state = CaseState(patient_id=await gateway.adopt_patient(patient_id))
            graph = build_graph(gateway, criteria, GuardedLLM(llm, vault), as_of)
            clock = time.perf_counter()
            try:
                await emit("node_started", current)
                async for chunk in graph.astream(state, stream_mode="updates"):
                    for node, update in chunk.items():  # a line of nodes: one update at a time
                        state = state.model_copy(update=update)
                        seconds[node] = round(time.perf_counter() - clock, 2)
                        summary = NODE_FUNCTIONS[node].summarize(update)
                        await emit("node_finished", node, {**summary, "seconds": seconds[node]})
                        clock = time.perf_counter()
                        if node != NODES[-1]:
                            current = NODES[NODES.index(node) + 1]
                            await emit("node_started", current)
            except Exception as err:
                await emit("node_failed", current, {
                    "error_type": type(err).__name__,
                    "error": gateway.anonymizer.scrub_message(str(err)),
                })
                raise
        return PipelineOutcome(
            state=state,
            packet=Packet.model_validate(rehydrate(state.packet.model_dump(), vault, state.patient_id)),
            verification=Verification.model_validate(
                rehydrate(state.verification.model_dump(), vault, state.patient_id)
            ),
            seconds=seconds,
        )
    finally:
        vault.clear()  # the re-ID map dies with the run, not whenever it is garbage collected
