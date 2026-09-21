"""Provider-neutral LLM interface. No provider package is imported here."""

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
    """The client for `settings.llm_provider`."""
    from app.llm.langchain_client import LangChainClient, build_chat_model

    if settings.llm_provider == "anthropic":
        provider, model, key = "anthropic", settings.anthropic_model, settings.anthropic_api_key
    else:
        provider, model, key = "gemini", settings.gemini_model, settings.gemini_api_key
    return LangChainClient(provider, build_chat_model(provider, model, key), model)
