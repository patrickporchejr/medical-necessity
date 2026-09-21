"""Run the whole pipeline for one patient and score the packet it produces.

    python evals/run.py --patient Loyd638 --provider both            # in-process MCP server
    python evals/run.py --patient Loyd638 --mcp-url http://localhost:8001/mcp   # the compose service

Run from the repo root so the one top-level .env is found. MCP server -> PHI gateway ->
extract -> assemble (a real model, behind the prompt guard) -> verify -> rehydrate -> score
against ground truth from the raw bundles. Traces go to LangSmith when a key is set.
"""

import argparse
import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from langsmith import trace
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from app.config import settings
from app.graph.build import build_graph
from app.graph.state import CaseState, Packet
from app.llm.client import LLMClient, build_client
from app.llm.guard import GuardedLLM
from app.observability import flush, setup_observability, tracing
from app.phi.anonymize import Anonymizer
from app.phi.gateway import PhiGateway
from app.phi.rehydrate import rehydrate
from app.phi.vault import Vault
from dataset import GroundTruth, open_ground_truth
from metrics.citation_resolution import score_packet

RUNS_DIR = Path(__file__).parent / "runs"
PROVIDERS = ("anthropic", "gemini")


@dataclass
class CaseResult:
    patient_id: str
    provider: str
    as_of: str
    model: str | None = None
    ok: bool = False
    error: str | None = None
    seconds: dict[str, float] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    fallback: bool = False
    citations_repaired: int = 0  # ids that lost their angle brackets and were restored
    packet: dict | None = None  # de-identified: exactly what the model wrote, placeholders and all
    assertions: list[dict] = field(default_factory=list)
    unaddressed: list[str] = field(default_factory=list)
    citation_resolution_rate: float | None = None
    citation_level_rate: float | None = None
    gap_accuracy: float | None = None
    verify_agrees_with_truth: bool | None = None


async def run_case(
    session: ClientSession, llm: LLMClient, patient_id: str, truth: GroundTruth, as_of: date
) -> tuple[CaseResult, Packet | None]:
    """One patient through one provider. Returns the result and the rehydrated packet (for
    display only; the result itself holds nothing but de-identified content)."""
    result = CaseResult(patient_id=patient_id, provider=llm.provider, as_of=as_of.isoformat())
    vault = Vault()
    gateway = PhiGateway(session, Anonymizer(vault))
    try:
        with tracing(), trace("eval.case", metadata={"provider": llm.provider}) as span:
            clock = time.perf_counter()

            def lap(stage: str) -> None:
                nonlocal clock
                result.seconds[stage] = round(time.perf_counter() - clock, 2)
                clock = time.perf_counter()

            state = CaseState(patient_id=await gateway.adopt_patient(patient_id))
            graph = build_graph(gateway, truth.criteria, GuardedLLM(llm, vault), as_of)
            async for chunk in graph.astream(state, stream_mode="updates"):
                for node, update in chunk.items():  # nodes run in a line: one lap per node
                    state = state.model_copy(update=update)
                    lap(node)
            usage, verification = state.llm_usage, state.verification

            real = Packet.model_validate(
                rehydrate(state.packet.model_dump(), vault, state.patient_id)
            )
            score = score_packet(real, patient_id, truth)

            by_verify = [v.supported for v in verification.assertions]
            by_truth = [a.resolved for a in score.assertions]
            result.model = state.assembled_by
            result.input_tokens, result.output_tokens = usage["input_tokens"], usage["output_tokens"]
            result.fallback = usage["fallback"]
            result.citations_repaired = usage["citations_repaired"]
            result.packet = state.packet.model_dump()
            result.assertions = [
                {
                    "criterion_id": v.assertion.criterion_id,
                    "kind": v.assertion.kind,
                    "verify_supported": v.supported,
                    "truth_resolved": s.resolved,
                    "reasons": v.reasons,
                }
                for v, s in zip(verification.assertions, score.assertions)
            ]
            result.unaddressed = verification.unaddressed
            result.citation_resolution_rate = score.citation_resolution_rate
            result.citation_level_rate = score.citation_level_rate
            result.gap_accuracy = score.gap_accuracy
            result.verify_agrees_with_truth = (
                by_verify == by_truth and verification.unaddressed == score.unaddressed
            )
            result.ok = True
            span.add_metadata(
                {
                    k: v
                    for k, v in {
                        "model": result.model,
                        "citation_resolution_rate": result.citation_resolution_rate,
                        "citation_level_rate": result.citation_level_rate,
                        "gap_accuracy": result.gap_accuracy,
                        "verify_agrees_with_truth": result.verify_agrees_with_truth,
                        "unaddressed": result.unaddressed,
                        "citations_repaired": result.citations_repaired,
                    }.items()
                    if v is not None
                }
            )
            return result, real
    except Exception as err:  # one provider failing must not lose the other's result
        result.error = f"{type(err).__name__}: {err}"
        return result, None


def resolve_patient(truth: GroundTruth, wanted: str) -> str:
    cases = truth.cases()
    exact = [c for c in cases if c.patient_id == wanted]
    matches = exact or [c for c in cases if wanted.lower() in c.name.lower()]
    if len(matches) != 1:
        found = ", ".join(c.name for c in matches[:5]) or "nothing"
        raise SystemExit(f"--patient {wanted!r} must match exactly one patient (matched: {found})")
    return matches[0].patient_id


@asynccontextmanager
async def connect(mcp_url: str | None):
    if mcp_url is None:
        from app.mcp.server import build_server

        server = build_server(settings.fhir_dir, settings.criteria_dir)
        async with create_connected_server_and_client_session(server._mcp_server) as session:
            yield session
    else:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(mcp_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def format_result(result: CaseResult, real: Packet | None) -> str:
    head = f"{result.provider}: {result.model or 'no model answered'}"
    if not result.ok:
        return f"{head}\n  FAILED  {result.error}"
    tokens = f"{result.input_tokens} in / {result.output_tokens} out"
    laps = " / ".join(f"{k} {v}s" for k, v in result.seconds.items())
    lines = [f"{head}   as of {result.as_of}   {laps}   {tokens}"
             + ("   [fallback model answered]" if result.fallback else "")]
    texts = {(a.criterion_id, a.kind): a.text for a in (real.assertions if real else [])}
    lines.append(f"  {'criterion':<24}{'kind':<10}{'verify':<8}{'truth':<7}claim")
    for a in result.assertions:
        text = texts.get((a["criterion_id"], a["kind"]), "")
        lines.append(
            f"  {a['criterion_id']:<24}{a['kind']:<10}{'ok' if a['verify_supported'] else 'FLAG':<8}"
            f"{'ok' if a['truth_resolved'] else 'FAIL':<7}{text[:90]}"
        )
        lines += [f"      - {reason}" for reason in a["reasons"]]
    if result.citations_repaired:
        lines.append(f"  ids restored to <ID> form: {result.citations_repaired}")
    if result.unaddressed:
        lines.append(f"  criteria never addressed: {', '.join(result.unaddressed)}")
    rate = lambda x: "n/a" if x is None else f"{x:.2f}"
    lines.append(
        f"  citation_resolution_rate {rate(result.citation_resolution_rate)} | "
        f"citation_level {rate(result.citation_level_rate)} | gap_accuracy {rate(result.gap_accuracy)} | "
        f"verify agrees with ground truth: {'yes' if result.verify_agrees_with_truth else 'NO'}"
    )
    return "\n".join(lines)


def write_results(results: list[CaseResult], meta: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}_{results[0].patient_id[:8]}.json"
    path.write_text(json.dumps({"meta": meta, "results": [asdict(r) for r in results]}, indent=2))
    return path


async def run(args, truth: GroundTruth, patient_id: str) -> list[CaseResult]:
    providers = list(PROVIDERS) if args.provider == "both" else [args.provider]
    results: list[CaseResult] = []
    with tracing(), trace("eval.run", metadata={"providers": providers, "mcp": args.mcp_url or "in-process"}):
        async with connect(args.mcp_url) as session:
            for provider in providers:
                llm = build_client(settings.model_copy(update={"llm_provider": provider}))
                result, real = await run_case(session, llm, patient_id, truth, settings.as_of)
                print(format_result(result, real), end="\n\n", flush=True)
                results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--patient", required=True, help="patient id, or a name fragment matching exactly one")
    parser.add_argument("--provider", choices=[*PROVIDERS, "both"], default=settings.llm_provider)
    parser.add_argument("--mcp-url", help="use a running MCP server instead of an in-process one")
    parser.add_argument("--out-dir", type=Path, default=RUNS_DIR)
    args = parser.parse_args(argv)

    mode = setup_observability(settings)
    truth = open_ground_truth(settings.as_of)
    patient_id = resolve_patient(truth, args.patient)
    print(f"patient {patient_id[:8]}, as of {settings.as_of}, traces: {mode}\n")

    results = asyncio.run(run(args, truth, patient_id))
    flush()

    meta = {"as_of": settings.as_of.isoformat(), "mcp": args.mcp_url or "in-process", "traces": mode}
    print(f"results: {write_results(results, meta, args.out_dir)}")
    return 0 if all(r.ok and r.verify_agrees_with_truth for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
