from collections.abc import AsyncIterator
from typing import Any, TypeVar

import litellm
from pydantic import BaseModel

from paperhub.llm.prompts.registry import PromptRegistry

T = TypeVar("T", bound=BaseModel)


class LiteLlmAdapter:
    def __init__(self, registry: PromptRegistry | None = None) -> None:
        self._registry = registry or PromptRegistry()

    def _messages(
        self,
        slot: str,
        variables: dict[str, Any],
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        prompt = self._registry.get(slot)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": prompt.system},
        ]
        if history:
            for h in history:
                role = h.get("role")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append(
            {"role": "user", "content": prompt.user_template.format(**variables)},
        )
        return messages

    async def structured(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        response_model: type[T],
        model: str,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> T:
        # Pass the Pydantic class directly so LiteLLM translates it into each
        # provider's native structured-output mode (Gemini responseSchema, OpenAI
        # json_schema, Anthropic tool-use shim). The model is then constrained
        # at the API boundary, not just by prompt phrasing.
        response = await litellm.acompletion(
            model=model,
            messages=self._messages(slot, variables, history),
            response_format=response_model,
            **kwargs,
        )
        content = response["choices"][0]["message"]["content"]
        return response_model.model_validate_json(content)

    async def stream(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        model: str,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        response = await litellm.acompletion(
            model=model,
            messages=self._messages(slot, variables, history),
            stream=True,
            **kwargs,
        )
        async for chunk in response:
            delta = chunk["choices"][0].get("delta", {}).get("content") or ""
            if delta:
                yield delta
