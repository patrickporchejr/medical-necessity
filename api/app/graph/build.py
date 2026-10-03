"""The case graph, as a LangGraph StateGraph, and the runner that puts one real patient
through it.

    extract -> assemble   -> verify     the gate criterion is established: a model drafts
            -> gap_packet -> verify     it is not: code writes the packet, no model call
                 ^            |
                 +------------+         verify flagged the model's packet: repair, at most
                                        `max_repairs` times

Two routing decisions. After `extract`: without an active RA diagnosis no packet can be
approved, so there is nothing for a model to weigh. After `verify`: a model-drafted packet with
flagged or unaddressed criteria goes back to `assemble` with verify's reasons and is verified
again, at most `max_repairs` times (once by default). Whatever is still flagged then stays
flagged for the reviewer. The nodes stay plain async functions that take their dependencies
as arguments; `build_graph` binds those dependencies for one case and wires the edges. The
gateway and the LLM are per case (each case has its own vault), so the graph is built per case
too. Compiling is cheap.

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
from app.graph.nodes.assemble import assemble, repair_targets
from app.graph.nodes.extract import ChartGateway, extract
from app.graph.nodes.gap_packet import gap_packet
from app.graph.nodes.verify import verify
from app.graph.state import CaseState, Packet, Verification
from app.graph.support import established
from app.llm.client import LLMClient
from app.llm.guard import GuardedLLM
from app.observability import tracing
from app.phi.anonymize import Anonymizer
from app.phi.gateway import PhiGateway
from app.phi.rehydrate import rehydrate
from app.phi.vault import Vault

GATE = "ra_diagnosis"  # the criterion every other one rests on
MAX_REPAIRS = 1  # times a flagged packet goes back to assemble before it is left to the reviewer


class ToolSession(Protocol):
    """What the runner needs of an MCP client session. Graph code never imports the MCP
    client itself: whoever calls the runner holds the session."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def route_after_extract(state: CaseState) -> Literal["assemble", "gap_packet"]:
    """Where a case goes once its evidence is in. A criteria file without the gate criterion
    always goes to the model."""
    gate = next((e for e in state.evidence if e.criterion_id == GATE), None)
    return "gap_packet" if gate is not None and not established(gate) else "assemble"


def route_after_verify(state: CaseState, criteria: Criteria, max_repairs: int) -> Literal["assemble", "__end__"]:
    """Back to the model while its packet has something to repair and repairs are left. A
    code-written gap packet is never sent to a model."""
    if state.route == "assemble" and state.repairs < max_repairs and repair_targets(state, criteria):
        return "assemble"
    return END


def build_graph(gateway: ChartGateway, criteria: Criteria, llm: LLMClient, as_of: date,
                max_repairs: int = MAX_REPAIRS):
    """`gateway` is the PHI gateway and `llm` is already behind the prompt guard: the graph
    adds no path to raw data or to an unguarded model."""

    async def extract_node(state: CaseState):
        return await extract(state, gateway, criteria, as_of)

    async def assemble_node(state: CaseState):
        return await assemble(state, criteria, llm)

    async def gap_packet_node(state: CaseState):
        return await gap_packet(state, criteria, GATE)

    async def verify_node(state: CaseState):
        return await verify(state, gateway, criteria)

    graph = StateGraph(CaseState)
    graph.add_node("extract", extract_node)
    graph.add_node("assemble", assemble_node)
    graph.add_node("gap_packet", gap_packet_node)
    graph.add_node("verify", verify_node)
    graph.add_edge(START, "extract")
    graph.add_conditional_edges("extract", route_after_extract, ["assemble", "gap_packet"])
    graph.add_edge("assemble", "verify")
    graph.add_edge("gap_packet", "verify")
    graph.add_conditional_edges(
        "verify", lambda state: route_after_verify(state, criteria, max_repairs), ["assemble", END]
    )
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
    seconds: dict[str, float]  # per node, summed over a node's runs when the repair loop ran
    draft_packet: Packet | None = None  # the model's first packet, rehydrated; None if no repair ran


EventHandler = Callable[[PipelineEvent], Awaitable[None] | None]
NODE_FUNCTIONS = {"extract": extract, "assemble": assemble, "gap_packet": gap_packet, "verify": verify}


async def run_pipeline(
    session: ToolSession,
    llm: LLMClient,
    patient_id: str,
    criteria: Criteria,
    as_of: date,
    on_event: EventHandler | None = None,
    max_repairs: int = MAX_REPAIRS,
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
    current: str | None = None
    try:
        with tracing():
            state = CaseState(patient_id=await gateway.adopt_patient(patient_id))
            graph = build_graph(gateway, criteria, GuardedLLM(llm, vault), as_of, max_repairs)
            try:
                # "tasks" reports each node as it starts and again when it finishes, so the
                # events follow whichever branch the graph took. A task's input is the whole
                # state: it is never read here, let alone sent.
                async for task in graph.astream(state, stream_mode="tasks"):
                    node = task["name"]
                    if "result" not in task:
                        current, clock = node, time.perf_counter()
                        await emit("node_started", node)
                        continue
                    update = task["result"]
                    state = state.model_copy(update=update)
                    lap = round(time.perf_counter() - clock, 2)
                    seconds[node] = round(seconds.get(node, 0.0) + lap, 2)
                    summary = NODE_FUNCTIONS[node].summarize(update)
                    await emit("node_finished", node, {**summary, "seconds": lap})
            except Exception as err:
                await emit("node_failed", current or "graph", {
                    "error_type": type(err).__name__,
                    "error": gateway.anonymizer.scrub_message(str(err)),
                })
                raise
        real = lambda model: rehydrate(model.model_dump(), vault, state.patient_id)
        return PipelineOutcome(
            state=state,
            packet=Packet.model_validate(real(state.packet)),
            verification=Verification.model_validate(real(state.verification)),
            seconds=seconds,
            draft_packet=None if state.draft_packet is None else Packet.model_validate(real(state.draft_packet)),
        )
    finally:
        vault.clear()  # the re-ID map dies with the run, not whenever it is garbage collected
