"""Provider-neutral LLM interface. No provider SDK is imported here."""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    """The provider declined the request (safety classifier or block)."""


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    parsed: T
    provider: str
    model: str  # the model that actually answered; may differ from the one requested
    input_tokens: int | None = None
    output_tokens: int | None = None
    fallback: bool = False  # the provider re-ran the request on another model


class LLMClient(Protocol):
    provider: str

    async def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        """One structured call: returns the model's answer validated as `schema`."""
        ...


def build_client(settings) -> LLMClient:
    """The client for `settings.llm_provider`. Imports lazily so the unused SDK is not loaded."""
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_client import AnthropicClient

        return AnthropicClient(model=settings.anthropic_model, api_key=settings.anthropic_api_key)
    from app.llm.gemini_client import GeminiClient

    return GeminiClient(model=settings.gemini_model, api_key=settings.gemini_api_key)
