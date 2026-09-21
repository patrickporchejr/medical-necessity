"""The provider clients, on LangChain chat models.

`ChatAnthropic` and `ChatGoogleGenerativeAI` do the transport; this adapter keeps the same
`LLMClient` contract the rest of the app uses: one structured call in, an `LLMResult` out, and
refusals and unparseable replies raised as errors rather than returned as empty packets.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.llm.client import LLMError, LLMRefusal, LLMResult, T

# `fallbacks: "default"` re-runs a request Claude Opus 5's safety classifiers decline on
# another model, server-side, instead of surfacing the refusal. Clinical text can trip them.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# What each model accepts. Haiku 4.5 has no `effort`; the refusal fallback is for the models
# whose classifiers can decline (Opus 5, Fable). Anything else would be rejected or pointless.
NO_EFFORT_PREFIXES = ("claude-haiku",)
FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable")


def build_chat_model(
    provider: str, model: str, api_key: str = "", effort: str = "medium", max_tokens: int = 16000
) -> BaseChatModel:
    """The chat model for one provider. An empty key falls through to the SDK's own
    credential lookup."""
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        options: dict[str, Any] = {}
        if not model.startswith(NO_EFFORT_PREFIXES):
            options["effort"] = effort
        if model.startswith(FALLBACK_PREFIXES):
            options.update(betas=[FALLBACK_BETA], model_kwargs={"fallbacks": "default"})
        return ChatAnthropic(
            model=model, max_tokens=max_tokens, api_key=api_key or None, **options
        )
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model, api_key=api_key or None)


class LangChainClient:
    def __init__(self, provider: str, model: BaseChatModel, model_name: str):
        self.provider = provider
        self._model, self._model_name = model, model_name

    async def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        chain = self._model.with_structured_output(schema, include_raw=True, method="json_schema")
        out = await chain.ainvoke([SystemMessage(system), HumanMessage(user)])
        raw: AIMessage = out["raw"]
        self._raise_if_declined(raw)
        if out.get("parsed") is None:
            raise LLMError(
                f"{self._model_name} returned output that does not match {schema.__name__}"
                f" ({out.get('parsing_error')})"
            )
        usage = raw.usage_metadata or {}
        meta = raw.response_metadata
        iterations = (meta.get("usage") or {}).get("iterations") or []
        return LLMResult(
            parsed=out["parsed"],
            provider=self.provider,
            model=meta.get("model_name") or meta.get("model") or self._model_name,
            input_tokens=usage.get("input_tokens"),
            # Gemini's thinking tokens are billed as output; LangChain folds them into this count.
            output_tokens=usage.get("output_tokens"),
            fallback=any(_field(i, "type") == "fallback_message" for i in iterations),
        )

    def _raise_if_declined(self, raw: AIMessage) -> None:
        meta = raw.response_metadata
        if meta.get("stop_reason") == "refusal":  # Anthropic
            category = _field(meta.get("stop_details"), "category")
            raise LLMRefusal(
                f"{meta.get('model_name') or self._model_name} refused the request (category: {category})"
            )
        feedback = meta.get("prompt_feedback") or {}
        block = feedback.get("block_reason") or (
            meta["finish_reason"] if meta.get("finish_reason") in _BLOCKED else None
        )
        if block:  # Gemini
            raise LLMRefusal(f"{self._model_name} returned no text (block reason: {block})")


_BLOCKED = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION", "IMAGE_SAFETY"}


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
