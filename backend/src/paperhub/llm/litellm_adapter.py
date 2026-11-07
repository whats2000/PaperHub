import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import litellm
from litellm.exceptions import BadRequestError
from pydantic import BaseModel

from paperhub.config import llm_timeout_s, small_tier_model
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.tracing.tracer import note_llm_usage, note_model_fallback

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Errors that a smaller model CANNOT fix — a malformed/oversized/forbidden
# request fails the same way on any tier, so we must NOT downgrade (it would
# just waste a second call and mask the real cause). Everything else (timeout,
# rate limit, 5xx, connection drop, model-not-found) is an AVAILABILITY signal:
# the requested model isn't usable right now, so downgrading to the small tier
# is worth a shot. Built by name so a litellm version missing one doesn't break.
_PERMANENT_EXC: tuple[type[BaseException], ...] = tuple(
    e
    for e in (
        getattr(litellm, "BadRequestError", None),
        getattr(litellm, "AuthenticationError", None),
        getattr(litellm, "PermissionDeniedError", None),
        getattr(litellm, "ContextWindowExceededError", None),
        getattr(litellm, "ContentPolicyViolationError", None),
    )
    if isinstance(e, type)
)


def _should_downgrade(exc: BaseException) -> bool:
    """True if ``exc`` is an availability failure worth retrying on a smaller
    model. Permanent client errors (bad request, auth, context-window, content
    policy) return False — a downgrade can't fix them."""
    return not isinstance(exc, _PERMANENT_EXC)


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


async def _acompletion_noted(*, model: str, **kwargs: Any) -> Any:
    """``litellm.acompletion`` that notes the call's token usage (reasoning
    tokens included) and latency on the active trace step."""
    started = time.monotonic()
    response = await litellm.acompletion(model=model, **kwargs)
    usage = response.get("usage") if isinstance(response, dict) else getattr(
        response, "usage", None)
    note_llm_usage(model, usage, int((time.monotonic() - started) * 1000))
    return response


class LiteLlmAdapter:
    def __init__(
        self,
        registry: PromptRegistry | None = None,
        *,
        fallback_model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._registry = registry or PromptRegistry()
        # The tier a failing flagship call downgrades to. Defaults to the
        # configured small tier (runtime-config-aware via env, which the DB
        # settings overlay projects onto). A call already on this model has no
        # lower tier, so it raises on failure (the provider is likely down).
        self._fallback_model = (
            fallback_model if fallback_model is not None else small_tier_model()
        )
        # Per-call wall-clock bound so an unavailable model fails FAST and we
        # downgrade promptly, instead of riding litellm's ~600 s default.
        self._timeout = timeout if timeout is not None else llm_timeout_s()

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
        """Structured output with flagship-first, downgrade-on-unavailable.

        Try ``model`` first; if it fails with an AVAILABILITY error (timeout,
        rate-limit, 5xx, connection, model-not-found), downgrade to the small
        tier for THIS call only and record the degrade on the trace step. A
        permanent error (bad request / auth) is re-raised immediately. If the
        fallback ALSO fails, that error propagates — the provider is likely down.
        The downgrade is per-call, so the next turn tries the flagship again.

        ``fallback_model`` (keyword) overrides the adapter's fallback tier for
        this call: ``""`` disables the downgrade, a model name replaces it."""
        messages = self._messages(slot, variables, history)
        timeout = kwargs.pop("timeout", self._timeout)
        fallback_override = kwargs.pop("fallback_model", None)
        base: dict[str, Any] = dict(kwargs)
        if timeout is not None:
            base["timeout"] = timeout

        fb = self._fallback_model if fallback_override is None else fallback_override
        if not fb or model == fb:
            # Already the small tier (or no fallback configured): no lower tier
            # to fall to, so a failure simply propagates.
            return await self._structured_call(messages, response_model, model, **base)

        # Flagship tier: fail FAST (no internal retries) so we downgrade promptly
        # rather than burning num_retries × timeout on a dead model.
        primary = {"num_retries": 0, **base}
        try:
            return await self._structured_call(messages, response_model, model, **primary)
        except Exception as exc:  # noqa: BLE001 — classified by _should_downgrade
            if not _should_downgrade(exc):
                raise
            logger.warning(
                "structured(%s): flagship %s unavailable (%s) — downgrading to %s",
                slot, model, type(exc).__name__, fb,
            )
            note_model_fallback(model, fb, f"{type(exc).__name__}: {exc}"[:200])
            fallback = {"num_retries": 1, **base}
            return await self._structured_call(messages, response_model, fb, **fallback)

    async def _structured_call(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        model: str,
        **kwargs: Any,
    ) -> T:
        """One structured call against a single ``model`` (native schema → json
        mode fallback). Provider/availability errors propagate to the caller's
        model-downgrade logic; only a json_schema-rejection is handled here."""
        # Providers with native structured output (OpenAI json_schema, Gemini
        # responseSchema, Anthropic tool-use): pass the Pydantic class directly so
        # the model is constrained at the API boundary, not just by prompt phrasing.
        if _supports_response_schema(model):
            try:
                response = await _acompletion_noted(
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
                    "structured: native response_format rejected by %s (%s); "
                    "falling back to json_object mode",
                    model, exc,
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
            response = await _acompletion_noted(
                model=model,
                messages=hinted,
                response_format={"type": "json_object"},
                **call_kwargs,
            )
        except BadRequestError:
            # Provider doesn't support json_object either — last resort: rely on
            # the schema instruction in the prompt with no response_format.
            response = await _acompletion_noted(
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
        # Streaming + transient-error retry-from-start + flagship-first model
        # downgrade. ``litellm.num_retries`` only catches errors BEFORE the
        # stream starts; we yield tokens AS THEY ARRIVE (real streaming), so
        # retry-from-start / downgrade is only safe while NOTHING has been
        # emitted yet — once a token reaches the caller, restarting would
        # duplicate output, so a mid-stream failure propagates. Within that
        # safe window: retry the SAME model on a transient blip, and if it's
        # genuinely unavailable, downgrade to the small tier for this call (and
        # record it on the trace). Permanent errors (bad request, auth)
        # propagate immediately without a downgrade.
        timeout = kwargs.pop("timeout", self._timeout)
        base: dict[str, Any] = dict(kwargs)
        if timeout is not None:
            base["timeout"] = timeout
        messages = self._messages(slot, variables, history)
        fb = self._fallback_model
        models = [model] + ([fb] if (fb and model != fb) else [])
        backoff_base = 1.0  # 1s, 2s, 4s
        last_exc: BaseException | None = None

        for mi, m in enumerate(models):
            has_fallback_left = mi < len(models) - 1
            # Fail the flagship FAST (fewer same-model retries) when a fallback
            # tier is still available, so the downgrade is prompt.
            max_attempts = 2 if has_fallback_left else 3
            for attempt in range(1, max_attempts + 1):
                emitted_any = False
                try:
                    response = await litellm.acompletion(
                        model=m, messages=messages, stream=True, **base,
                    )
                    async for chunk in response:
                        delta = chunk["choices"][0].get("delta", {}).get("content") or ""
                        if delta:
                            emitted_any = True
                            yield delta
                    return
                except Exception as exc:  # noqa: BLE001 — classified below
                    last_exc = exc
                    if emitted_any:
                        # Partial output already delivered — restart/downgrade
                        # would duplicate it. Propagate.
                        raise
                    if attempt < max_attempts and _is_transient_stream_error(exc):
                        await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
                        continue
                    break  # same-model attempts spent (nothing emitted)
            # Downgrade to the next tier only on an availability failure.
            if has_fallback_left and last_exc is not None and _should_downgrade(last_exc):
                nxt = models[mi + 1]
                logger.warning(
                    "stream(%s): %s unavailable (%s) — downgrading to %s",
                    slot, m, type(last_exc).__name__, nxt,
                )
                note_model_fallback(m, nxt, f"{type(last_exc).__name__}: {last_exc}"[:200])
                continue
            break  # permanent error, or no fallback left → stop
        # Defensive — the loop above either returns or raises.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("stream retry loop fell through without yielding")
