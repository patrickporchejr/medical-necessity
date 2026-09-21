"""Tracing, with the PHI boundary as the design constraint.

Spans are written by us and carry metadata only: counts, criterion ids, model names, token
counts, placeholders. They are recorded around graph nodes, which see only de-identified
data. Auto-instrumenting anything that sees raw data (the MCP client or server, HTTP
clients, FastAPI request bodies) is deliberately not done: a test enforces that.

The one exception is opt-in. The provider SDK instrumentations record the whole prompt and
response as span attributes, and Logfire retains them, unlike a zero-retention LLM call.
`LOGFIRE_CAPTURE_LLM_CONTENT` turns them on; prompts are de-identified and checked by the
guard first, but leave it off unless you are debugging with synthetic data.
"""

import functools
from typing import Any, Callable

import logfire


def _allow_agent_description(match: "logfire.ScrubMatch"):
    """Logfire redacts any value matching its secret-looking patterns, and `auth` is one of
    them, so our own agent description ("prior authorization") was blanked out. Allow exactly
    that attribute and that match; every other rule and every other attribute is untouched."""
    if match.path == ("attributes", "gen_ai.agent.description") and match.pattern_match.group(0).lower() == "auth":
        return match.value
    return None


def setup_observability(settings) -> str:
    """Configure Logfire once at startup. Returns "cloud" or "local" for what it will do."""
    cloud = bool(settings.logfire_token)
    logfire.configure(
        service_name="medical-necessity",
        send_to_logfire=cloud,
        token=settings.logfire_token or None,
        console=None if settings.logfire_console else False,
        metrics=False,
        scrubbing=logfire.ScrubbingOptions(callback=_allow_agent_description),
    )
    if settings.logfire_capture_llm_content:
        if settings.llm_provider == "anthropic":
            logfire.instrument_anthropic()
        else:
            logfire.instrument_google_genai()
    return "cloud" if cloud else "local"


# our provider name -> (OpenTelemetry gen_ai.provider.name, genai-prices provider id)
GENAI_PROVIDERS = {"anthropic": ("anthropic", "anthropic"), "gemini": ("gcp.gemini", "google")}


def agent_span(name: str, provider: str, description: str):
    """Register a node that calls a model as an agent, using the OpenTelemetry GenAI agent
    conventions Logfire's Agents page matches on. Metadata only: no prompt or response text."""
    otel_provider = GENAI_PROVIDERS.get(provider, (provider, provider))[0]
    # Concatenated, not an f-string: Logfire rewrites f-strings into "invoke_agent {name}"
    # templates, and the convention wants the literal span name "invoke_agent assemble".
    return logfire.span(
        "invoke_agent " + name,
        **{
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": name,
            "gen_ai.agent.description": description,
            "gen_ai.provider.name": otel_provider,
        },
    )


def record_agent_usage(span, result) -> None:
    """Model, tokens and cost for an agent span, from an LLMResult. Logfire shows cost from
    the `operation.cost` attribute, which its own SDK integrations compute client-side with
    genai-prices; we do the same. An unknown model just means no cost, never an error."""
    attrs: dict[str, Any] = {
        "gen_ai.request.model": result.model,
        "gen_ai.response.model": result.model,  # the model that answered, which fallbacks can change
    }
    if result.input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = result.input_tokens
    if result.output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = result.output_tokens
    cost = estimate_cost(result)
    if cost is not None:
        attrs["operation.cost"] = cost
    span.set_attributes(attrs)


def estimate_cost(result) -> float | None:
    if result.input_tokens is None or result.output_tokens is None:
        return None
    try:
        from genai_prices import Usage, calc_price

        price_id = GENAI_PROVIDERS.get(result.provider, (None, result.provider))[1]
        price = calc_price(
            Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
            model_ref=result.model,
            provider_id=price_id,
        )
        return float(price.total_price)
    except Exception:
        return None


def node_span(name: str, summarize: Callable[[dict[str, Any]], dict[str, Any]]):
    """Wrap a graph node in a span. `summarize` maps the node's returned update to the
    attributes to record; it is the allowlist, so it must return metadata only."""

    def decorate(node):
        @functools.wraps(node)
        async def wrapper(state, *args, **kwargs):
            with logfire.span(name, patient=state.patient_id) as span:
                update = await node(state, *args, **kwargs)
                span.set_attributes(summarize(update))
                return update

        return wrapper

    return decorate
