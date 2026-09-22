"""Tracing through LangSmith, with the PHI boundary as the design constraint.

LangGraph and LangChain trace every run they touch, inputs and outputs included. Left alone
that would send each node's whole state and each model call's prompt and reply to LangSmith,
which retains them. So the LangSmith client used here has hide functions that keep an
allowlist of keys and drop everything else. What is left is what we write ourselves: run
names, timings, errors, and the metadata set by `node_span` and `agent_span` (counts,
criterion ids, model names, token counts, placeholders).

Everything that traces runs inside `tracing()`, which binds that client. Graph nodes see only
de-identified data; nothing that sees raw data (the MCP client or server, HTTP clients,
request bodies) is traced, and a test enforces it.

`LANGSMITH_CAPTURE_CONTENT` is the opt-in. It sends whole states, prompts and replies
(de-identified, and checked by the guard first) and LangSmith retains them, so leave it off
unless you are debugging with synthetic data.
"""

import functools
from contextlib import contextmanager
from typing import Any, Callable

from langsmith import Client, trace, traceable
from langsmith.run_helpers import get_current_run_tree, tracing_context

# The only input and output keys that reach LangSmith by default. Eval experiments use them:
# a case's input is its short patient key, its output is the scored, de-identified result.
# None of these may also be a graph-state key, or LangGraph's own runs would carry it through;
# a test checks that.
SAFE_INPUT_KEYS = frozenset({"patient_key"})
SAFE_OUTPUT_KEYS = frozenset({
    "ok", "error", "provider", "model", "seconds", "input_tokens", "output_tokens",
    "fallback", "citations_repaired", "assertions", "unaddressed", "citation_resolution_rate",
    "citation_level_rate", "gap_accuracy", "verify_agrees_with_truth",
})

_client: Client | None = None
_project: str | None = None


def _allowlist(keys: frozenset[str]) -> Callable[[dict], dict]:
    return lambda payload: {k: v for k, v in payload.items() if k in keys}


def build_client(settings, **client_options) -> Client:
    keep_all = settings.langsmith_capture_content
    return Client(
        api_url=settings.langsmith_endpoint or None,
        api_key=settings.langsmith_api_key,
        hide_inputs=None if keep_all else _allowlist(SAFE_INPUT_KEYS),
        hide_outputs=None if keep_all else _allowlist(SAFE_OUTPUT_KEYS),
        **client_options,
    )


def setup_observability(settings) -> str:
    """Configure LangSmith once at startup. Returns "cloud" (traces are sent) or "off"."""
    global _client, _project
    _client = build_client(settings) if settings.langsmith_api_key else None
    _project = settings.langsmith_project
    return "cloud" if _client else "off"


def get_client() -> Client | None:
    return _client


@contextmanager
def tracing():
    """Bind tracing for whatever runs inside: on, through the metadata-only client, if
    `setup_observability` found a key; explicitly off otherwise, even if LANGSMITH_TRACING is
    set in the shell, because an ambient client would record whole states."""
    if _client is None:
        with tracing_context(enabled=False):
            yield
    else:
        with tracing_context(enabled=True, client=_client, project_name=_project):
            yield


def flush() -> None:
    if _client is not None:
        _client.flush()


# our provider name -> (LangSmith ls_provider, genai-prices provider id)
PROVIDERS = {"anthropic": ("anthropic", "anthropic"), "gemini": ("google_genai", "google")}


def agent_span(name: str, provider: str, description: str):
    """A model-calling node as one run, so it can be found by name. Metadata only: no prompt or
    response text. Tokens and cost are not set here: the chat model's own run reports them, and
    a second count on this parent would double every total in LangSmith."""
    return trace(
        name,
        run_type="chain",
        inputs={},
        metadata={"provider": PROVIDERS.get(provider, (provider, provider))[0],
                  "agent": name, "description": description},
    )


def record_agent_usage(span, result) -> None:
    """The model that answered, and our own cost estimate, on the agent run. An unknown model
    just means no estimate, never an error."""
    meta: dict[str, Any] = {"model": result.model}
    cost = estimate_cost(result)
    if cost is not None:
        meta["estimated_cost_usd"] = cost
    span.add_metadata(meta)


def estimate_cost(result) -> float | None:
    if result.input_tokens is None or result.output_tokens is None:
        return None
    try:
        from genai_prices import Usage, calc_price

        price_id = PROVIDERS.get(result.provider, (None, result.provider))[1]
        price = calc_price(
            Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
            model_ref=result.model,
            provider_id=price_id,
        )
        return float(price.total_price)
    except Exception:
        return None


def node_span(name: str, summarize: Callable[[dict[str, Any]], dict[str, Any]]):
    """Wrap a graph node in a run. `summarize` maps the node's returned update to the metadata
    to record; it is the allowlist, so it must return metadata only."""

    def decorate(node):
        @functools.wraps(node)
        async def wrapper(state, *args, **kwargs):
            @traceable(name=name, run_type="chain", metadata={"patient": state.patient_id},
                       process_inputs=lambda _: {}, process_outputs=lambda _: {})
            async def run():
                update = await node(state, *args, **kwargs)
                if (current := get_current_run_tree()) is not None:
                    current.add_metadata(summarize(update))
                return update

            return await run()

        wrapper.summarize = summarize  # the same metadata, for whoever reports on the node (events)
        return wrapper

    return decorate
