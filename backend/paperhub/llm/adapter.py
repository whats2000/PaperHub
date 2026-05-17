"""LLM Provider Adapter — LiteLLM-backed production + FakeAdapter for tests.

`LlmAdapter` is the Protocol every agent depends on; it returns a typed
Pydantic instance via the provider's structured-output mode.

Production: `LiteLlmAdapter` wraps `litellm.acompletion()` with
`response_format={"type": "json_schema", ...}` so Anthropic / OpenAI /
Ollama all expose the same typed contract.

Tests: `FakeAdapter` returns canned Pydantic instances keyed by slot,
so agent unit tests don't touch the network.
"""

from __future__ import annotations

import json
from typing import Literal, Protocol, TypeVar

import litellm
from pydantic import BaseModel

ModelTier = Literal["small", "flagship"]
LlmRole = Literal["system", "user", "assistant"]


class LlmMessage(BaseModel):
    role: LlmRole
    content: str


T = TypeVar("T", bound=BaseModel)


class LlmAdapter(Protocol):
    """A pluggable LLM provider exposing one typed structured-output call."""

    async def generate(
        self,
        *,
        messages: list[LlmMessage],
        model_tier: ModelTier,
        response_model: type[T],
        slot: str,
    ) -> T: ...


class FakeAdapter:
    """Test-time double. Returns canned Pydantic instances keyed by `slot`."""

    def __init__(self, canned: dict[str, BaseModel]) -> None:
        self._canned = canned

    async def generate(
        self,
        *,
        messages: list[LlmMessage],
        model_tier: ModelTier,
        response_model: type[T],
        slot: str,
    ) -> T:
        if slot not in self._canned:
            raise KeyError(f"No canned response for slot {slot!r}")
        value = self._canned[slot]
        if not isinstance(value, response_model):
            raise TypeError(
                f"Canned value for slot {slot!r} is {type(value).__name__}, "
                f"expected {response_model.__name__}"
            )
        return value


class LiteLlmAdapter:
    """Production adapter: structured output across Anthropic / OpenAI / Ollama via LiteLLM."""

    def __init__(self, *, small_model: str, flagship_model: str) -> None:
        self._small = small_model
        self._flagship = flagship_model

    async def generate(
        self,
        *,
        messages: list[LlmMessage],
        model_tier: ModelTier,
        response_model: type[T],
        slot: str,
    ) -> T:
        model = self._small if model_tier == "small" else self._flagship
        response = await litellm.acompletion(
            model=model,
            messages=[m.model_dump() for m in messages],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": slot,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            },
        )
        content = response.choices[0].message.content
        if not isinstance(content, str):
            raise TypeError(f"LiteLLM returned non-string content for slot {slot!r}")
        return response_model.model_validate(json.loads(content))
