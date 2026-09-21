from google import genai
from google.genai import types
from pydantic import ValidationError

from app.llm.client import LLMError, LLMRefusal, LLMResult, T


class GeminiClient:
    provider = "gemini"

    def __init__(self, model: str = "gemini-3.8-flash", api_key: str | None = None, client=None):
        self._model = model
        # An empty key falls through to the SDK's own environment lookup.
        self._client = client or genai.Client(api_key=api_key or None)

    async def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
            ),
        )
        if not response.text:
            block = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
            raise LLMRefusal(f"{self._model} returned no text (block reason: {block})")
        try:
            parsed = schema.model_validate_json(response.text)
        except ValidationError as err:
            raise LLMError(f"{self._model} returned JSON that does not match {schema.__name__}") from err
        usage = response.usage_metadata
        # Thinking tokens are billed as output but reported apart from the answer. Leaving them
        # out understated Flash's output by ~4x on a real prompt (330 answer + 1229 thinking).
        answer = getattr(usage, "candidates_token_count", None)
        thinking = getattr(usage, "thoughts_token_count", None)
        output_tokens = None if answer is None else answer + (thinking or 0)
        return LLMResult(
            parsed=parsed,
            provider=self.provider,
            model=getattr(response, "model_version", None) or self._model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=output_tokens,
        )
