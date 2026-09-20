"""Last line of defence on the PHI boundary: nothing leaves for a provider if it holds a
value the vault knows to be real."""

import re

from app.llm.client import LLMClient, LLMResult, T
from app.phi.vault import Vault

MIN_LENGTH = 3


class PhiLeak(RuntimeError):
    """A prompt contained a real identifier. The message names the kind, never the value."""


def assert_clean(text: str, vault: Vault) -> None:
    for kind, original in vault.known():
        if len(original) >= MIN_LENGTH and re.search(
            rf"(?<!\w){re.escape(original)}(?!\w)", text, re.IGNORECASE
        ):
            raise PhiLeak(f"Prompt contains a real {kind} value; refusing to send it")


class GuardedLLM:
    """Wraps any client so every prompt is checked against the vault before it is sent."""

    def __init__(self, client: LLMClient, vault: Vault):
        self._client, self._vault = client, vault
        self.provider = client.provider

    async def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        assert_clean(system, self._vault)
        assert_clean(user, self._vault)
        return await self._client.generate(system, user, schema)
