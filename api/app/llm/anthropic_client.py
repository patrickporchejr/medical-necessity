import anthropic

from app.llm.client import LLMError, LLMRefusal, LLMResult, T

# `fallbacks: "default"` re-runs a request Claude Opus 5's safety classifiers decline on
# another model, server-side, instead of surfacing the refusal. Clinical text can trip them.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# What each model accepts. Haiku 4.5 has no `effort`; the refusal fallback is for the models
# whose classifiers can decline (Opus 5, Fable). Anything else would be rejected or pointless.
NO_EFFORT_PREFIXES = ("claude-haiku",)
FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable")


class AnthropicClient:
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        api_key: str | None = None,
        effort: str = "medium",
        max_tokens: int = 16000,
        client: anthropic.AsyncAnthropic | None = None,
    ):
        self._model, self._effort, self._max_tokens = model, effort, max_tokens
        # An empty key falls through to the SDK's own credential resolution.
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key or None)

    async def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        extra: dict = {}
        if not self._model.startswith(NO_EFFORT_PREFIXES):
            extra["output_config"] = {"effort": self._effort}
        if self._model.startswith(FALLBACK_PREFIXES):
            extra.update(betas=[FALLBACK_BETA], fallbacks="default")
        response = await self._client.beta.messages.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            **extra,
        )
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMRefusal(f"{response.model} refused the request (category: {category})")
        if response.parsed_output is None:
            raise LLMError(f"No parseable output (stop_reason: {response.stop_reason})")
        iterations = getattr(response.usage, "iterations", None) or []
        return LLMResult(
            parsed=response.parsed_output,
            provider=self.provider,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            fallback=any(getattr(i, "type", None) == "fallback_message" for i in iterations),
        )
