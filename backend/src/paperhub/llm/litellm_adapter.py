import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import litellm
from litellm.exceptions import BadRequestError
from pydantic import BaseModel

from paperhub.llm.prompts.registry import PromptRegistry

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _supports_response_schema(model: str) -> bool:
    """True if the provider accepts a Pydantic/json_schema ``response_format``.

    Providers split into two camps: those with native structured output
    (OpenAI ``json_schema``, Gemini ``responseSchema``, Anthropic tool-use)
    and those that only support plain JSON mode (``response_format={"type":
    "json_object"}``) — DeepSeek, for one. Passing a Pydantic class to the
    latter raises ``litellm.BadRequestError`` (issue #4). litellm's model
    registry knows the distinction; on an unknown model we assume NO native
    support so the JSON-mode fallback (which works for both camps) is used.
    """
    try:
        return bool(litellm.supports_response_schema(model=model))
    except Exception:  # noqa: BLE001 — unknown model / registry miss → safe default
        return False


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$")


def _extract_json_object(text: str) -> str:
    """Pull a JSON object out of a model response.

    In ``json_object`` mode the content is already pure JSON, but the
    last-resort no-``response_format`` path relies on prompt phrasing, where a
    model may wrap the object in a ```json fence or surround it with prose.
    Strip a fence, else take the substring between the first ``{`` and last
    ``}``; fall back to the raw text so ``model_validate_json`` raises a
    meaningful error on genuine garbage.
    """
    s = text.strip()
    fence = _JSON_FENCE_RE.match(s)
    if fence:
        s = fence.group(1).strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        return s[start : end + 1]
    return s


# Connection-drop signatures we treat as recoverable in mid-stream — same
# class as the non-streaming ``litellm.num_retries`` catches, but applied
# manually because num_retries doesn't restart streaming responses.
_TRANSIENT_STREAM_SUBSTRINGS: tuple[str, ...] = (
    "Server disconnected",
    "MidStreamFallbackError",
    "APIConnectionError",
    "ServerDisconnectedError",
    "ConnectError",
    "RemoteProtocolError",
    "ReadTimeout",
    "ConnectTimeout",
    "503",
    "504",
    "502",
)


def _is_transient_stream_error(exc: BaseException) -> bool:
    """True if the exception looks like a recoverable upstream connection drop.

    Matches by class name + string content (litellm wraps provider errors in
    its own class hierarchy, so isinstance checks against httpx/openai types
    are unreliable). False positives just trigger an extra retry which is
    cheap; false negatives lose work which is expensive.
    """
    needle = type(exc).__name__ + ": " + str(exc)
    return any(s in needle for s in _TRANSIENT_STREAM_SUBSTRINGS)


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
        messages = self._messages(slot, variables, history)
        # Providers with native structured output (OpenAI json_schema, Gemini
        # responseSchema, Anthropic tool-use): pass the Pydantic class directly so
        # the model is constrained at the API boundary, not just by prompt phrasing.
        if _supports_response_schema(model):
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    response_format=response_model,
                    **kwargs,
                )
                content = response["choices"][0]["message"]["content"]
                return response_model.model_validate_json(content)
            except BadRequestError as exc:
                # The registry claimed json_schema support but the provider
                # rejected it (drift / partial support). Fall back to JSON mode.
                logger.warning(
                    "structured(%s): native response_format rejected by %s (%s); "
                    "falling back to json_object mode",
                    slot, model, exc,
                )
        # JSON-mode fallback for DeepSeek-class providers (issue #4): the schema
        # is injected into the prompt (no API-level enforcement available) and
        # the response is parsed + validated client-side.
        return await self._structured_json_mode(
            messages=messages, response_model=response_model, model=model, **kwargs,
        )

    async def _structured_json_mode(
        self,
        *,
        messages: list[dict[str, str]],
        response_model: type[T],
        model: str,
        **kwargs: Any,
    ) -> T:
        """Structured output via plain JSON mode for providers without json_schema.

        The exact JSON Schema is appended to the final user message so the model
        knows the target shape, ``response_format={"type": "json_object"}`` forces
        valid JSON where supported, and the result is validated against the
        Pydantic model. If even json_object mode is unsupported, retry once with
        no ``response_format`` and rely on the prompt instruction alone.
        """
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        hinted = list(messages)
        hinted[-1] = {
            **hinted[-1],
            "content": (
                hinted[-1]["content"]
                + "\n\nRespond with ONLY a single JSON object conforming exactly to "
                + "this JSON Schema. No prose, no markdown fences.\nJSON Schema:\n"
                + schema
            ),
        }
        # Don't let a caller-supplied response_format collide with ours.
        call_kwargs = {k: v for k, v in kwargs.items() if k != "response_format"}
        try:
            response = await litellm.acompletion(
                model=model,
                messages=hinted,
                response_format={"type": "json_object"},
                **call_kwargs,
            )
        except BadRequestError:
            # Provider doesn't support json_object either — last resort: rely on
            # the schema instruction in the prompt with no response_format.
            response = await litellm.acompletion(
                model=model, messages=hinted, **call_kwargs,
            )
        content = response["choices"][0]["message"]["content"]
        return response_model.model_validate_json(_extract_json_object(content))

    async def stream(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        model: str,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        # Streaming + transient-error retry-from-start. ``litellm.num_retries``
        # only catches errors BEFORE the stream starts; mid-stream
        # ``MidStreamFallbackError`` / ``APIConnectionError`` / 5xx are not
        # retried by litellm itself because the partial stream can't be
        # resumed. Wrap the whole stream in a retry loop: on transient mid-
        # stream failure, discard any partial yield and restart from scratch.
        # Permanent errors (bad request, auth) propagate immediately.
        max_attempts = 3
        backoff_base = 1.0  # 1s, 2s, 4s
        last_exc: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            buffered: list[str] = []
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=self._messages(slot, variables, history),
                    stream=True,
                    **kwargs,
                )
                async for chunk in response:
                    delta = chunk["choices"][0].get("delta", {}).get("content") or ""
                    if delta:
                        buffered.append(delta)
                # Stream completed successfully; flush buffered tokens to the
                # caller in one go. (We could not yield incrementally because
                # the caller would already have consumed partial tokens if we
                # then had to retry. Buffering trades a small latency hit for
                # crash-free resilience.)
                for tok in buffered:
                    yield tok
                return
            except Exception as exc:
                last_exc = exc
                if attempt >= max_attempts or not _is_transient_stream_error(exc):
                    raise
                await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
        # Defensive — the loop above either returns or raises.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("stream retry loop fell through without yielding")
