import json
from typing import Any

import aiosqlite
import litellm
import pytest

from paperhub.db.migrate import apply_schema
from paperhub.llm.adapter import LlmAdapter
from paperhub.llm.litellm_adapter import (
    LiteLlmAdapter,
    _extract_json_object,
    _should_downgrade,
)
from paperhub.llm.prompts.registry import PromptRegistry
from paperhub.models.domain import RoutingDecision
from paperhub.tracing.tracer import Tracer

_ROUTER_VARS = {"user_message": "find papers on MoE", "enabled_refs_count": 0,
                "slide_attached": False}

_VALID_DECISION = (
    '{"intent":"paper_search",'
    '"confidence":0.9,"reasoning":"asks to find papers"}'
)


class _FakeAcompletion:
    """Spy that records call kwargs and emulates a provider that rejects a
    Pydantic ``response_format`` (json_schema) but accepts json_object mode."""

    def __init__(self, content: str, *, reject_schema: bool = False) -> None:
        self.content = content
        self.reject_schema = reject_schema
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        rf = kwargs.get("response_format")
        # A Pydantic class (not a dict) is the json_schema request.
        if self.reject_schema and rf is not None and not isinstance(rf, dict):
            raise litellm.BadRequestError(
                message="json_schema not supported",
                model=kwargs.get("model", ""),
                llm_provider="deepseek",
            )
        return {"choices": [{"message": {"content": self.content}}]}


async def test_registry_loads_versioned_slot() -> None:
    reg = PromptRegistry()
    slot = reg.get("router/v1")
    assert slot.system.strip().startswith("You are PaperHub's intent router")
    assert "{user_message}" in slot.user_template


async def test_structured_output_parses_into_model() -> None:
    adapter: LlmAdapter = LiteLlmAdapter()
    decision = await adapter.structured(
        slot="router/v1",
        variables={"user_message": "Find recent papers on MoE routing",
                   "enabled_refs_count": 0, "slide_attached": False},
        response_model=RoutingDecision,
        model="gpt-4o-mini",
        mock_response='{"intent":"paper_search",'
                      '"confidence":0.91,"reasoning":"asks to find papers"}',
    )
    assert decision.intent == "paper_search"
    assert 0 <= decision.confidence <= 1


async def test_stream_yields_tokens() -> None:
    adapter: LlmAdapter = LiteLlmAdapter()
    chunks: list[str] = []
    async for token in adapter.stream(
        slot="chitchat/v1",
        variables={
            "user_message": "hi",
            "response_language": "English",
            "memory_context": "",
        },
        model="gpt-4o-mini",
        mock_response="Hello there!",
    ):
        chunks.append(token)
    assert "".join(chunks) == "Hello there!"


def _chunk(content: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": content}}]}


async def _no_sleep(_seconds: float) -> None:
    """Skip the retry backoff in tests."""
    return None


_CHITCHAT_VARS = {"user_message": "hi", "response_language": "English", "memory_context": ""}


async def test_stream_emits_tokens_live_not_buffered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streaming must reach the caller token-by-token, not buffered and flushed
    all-at-once at end-of-stream (the 'instant message, no streaming' bug).

    Detection: a fake upstream records each chunk as it is produced. After the
    caller pulls only the FIRST token, a live adapter has pulled exactly one
    chunk from upstream; a buffering adapter has already drained all of them.
    """
    produced: list[str] = []

    async def fake_acompletion(**kwargs: Any):  # noqa: ANN003
        async def gen():
            for c in ["A", "B", "C"]:
                produced.append(c)
                yield _chunk(c)
        return gen()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    adapter = LiteLlmAdapter()
    agen = adapter.stream(slot="chitchat/v1", variables=_CHITCHAT_VARS, model="gpt-4o-mini")
    first = await agen.__anext__()
    assert first == "A"
    assert produced == ["A"], (
        f"adapter buffered the whole stream before yielding the first token: {produced}"
    )
    rest = [t async for t in agen]
    assert "".join([first, *rest]) == "ABC"


async def test_stream_retries_transient_error_before_first_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resilience preserved: a transient failure BEFORE any token reaches the
    caller is still retried from scratch (restart is safe — nothing emitted)."""
    attempts = {"n": 0}

    async def fake_acompletion(**kwargs: Any):  # noqa: ANN003
        attempts["n"] += 1

        async def gen():
            if attempts["n"] == 1:
                raise litellm.APIConnectionError(
                    message="Connection error.", model="gpt-4o-mini", llm_provider="openai",
                )
                yield  # pragma: no cover - unreachable, makes gen an async generator
            for c in ["X", "Y"]:
                yield _chunk(c)
        return gen()

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    adapter = LiteLlmAdapter()
    out = [t async for t in adapter.stream(
        slot="chitchat/v1", variables=_CHITCHAT_VARS, model="gpt-4o-mini",
    )]
    assert "".join(out) == "XY"
    assert attempts["n"] == 2, "transient pre-token error should have retried once"


async def test_structured_uses_json_mode_when_no_native_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #4: a provider without json_schema support is driven via json_object
    mode with the schema injected into the prompt, then validated client-side."""
    fake = _FakeAcompletion(_VALID_DECISION)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake)

    adapter: LlmAdapter = LiteLlmAdapter()
    decision = await adapter.structured(
        slot="router/v1",
        variables={"user_message": "find papers on MoE", "enabled_refs_count": 0,
                   "slide_attached": False},
        response_model=RoutingDecision,
        model="deepseek/deepseek-v4-flash",
    )

    assert decision.intent == "paper_search"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    # json_object mode, NOT a Pydantic class.
    assert call["response_format"] == {"type": "json_object"}
    # The JSON Schema was appended to the final (user) message.
    last_msg = call["messages"][-1]
    assert last_msg["role"] == "user"
    assert "JSON Schema" in last_msg["content"]
    assert '"intent"' in last_msg["content"]


async def test_structured_falls_back_when_native_schema_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #4: even when the registry CLAIMS json_schema support, a provider
    that rejects it at request time falls back to json_object mode."""
    fake = _FakeAcompletion(_VALID_DECISION, reject_schema=True)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    monkeypatch.setattr(litellm, "acompletion", fake)

    adapter: LlmAdapter = LiteLlmAdapter()
    decision = await adapter.structured(
        slot="router/v1",
        variables={"user_message": "find papers on MoE", "enabled_refs_count": 0,
                   "slide_attached": False},
        response_model=RoutingDecision,
        model="deepseek/deepseek-chat",
    )

    assert decision.intent == "paper_search"
    # Two calls: the rejected native attempt, then the json_object fallback.
    assert len(fake.calls) == 2
    assert fake.calls[0]["response_format"] is RoutingDecision
    assert fake.calls[1]["response_format"] == {"type": "json_object"}


def test_extract_json_object_strips_fences_and_prose() -> None:
    assert _extract_json_object('{"a":1}') == '{"a":1}'
    assert _extract_json_object('```json\n{"a":1}\n```') == '{"a":1}'
    assert _extract_json_object('```\n{"a":1}\n```') == '{"a":1}'
    assert _extract_json_object('Here you go: {"a":1} done.') == '{"a":1}'


# ---------------------------------------------------------------------------
# Model fallback: flagship-first, downgrade-on-unavailable, raise if both fail
# ---------------------------------------------------------------------------

def _timeout(model: str) -> litellm.Timeout:
    return litellm.Timeout(message="timed out", model=model, llm_provider="gemini")


async def test_structured_downgrades_to_small_when_flagship_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flagship availability failure (timeout) downgrades to the small tier
    for this call; the flagship is tried FIRST, then the fallback."""
    calls: list[str] = []

    async def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["model"])
        if kwargs["model"] == "flagship/x":
            raise _timeout("flagship/x")
        return {"choices": [{"message": {"content": _VALID_DECISION}}]}

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake)

    adapter = LiteLlmAdapter(fallback_model="small/y")
    decision = await adapter.structured(
        slot="router/v1", variables=_ROUTER_VARS,
        response_model=RoutingDecision, model="flagship/x",
    )
    assert decision.intent == "paper_search"
    assert calls == ["flagship/x", "small/y"]  # flagship first, then downgrade


async def test_structured_raises_when_both_tiers_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the small tier ALSO fails, the error propagates — the provider is
    likely down; there is no dumb fallback."""
    async def fake(**kwargs: Any) -> dict[str, Any]:
        raise _timeout(kwargs["model"])

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake)

    adapter = LiteLlmAdapter(fallback_model="small/y")
    with pytest.raises(litellm.Timeout):
        await adapter.structured(
            slot="router/v1", variables=_ROUTER_VARS,
            response_model=RoutingDecision, model="flagship/x",
        )


async def test_structured_does_not_downgrade_on_permanent_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanent error (auth) is NOT downgraded — a smaller model can't fix
    it, so it raises immediately with no second-tier call."""
    calls: list[str] = []

    async def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["model"])
        raise litellm.AuthenticationError(
            message="bad key", model=kwargs["model"], llm_provider="gemini",
        )

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake)

    adapter = LiteLlmAdapter(fallback_model="small/y")
    with pytest.raises(litellm.AuthenticationError):
        await adapter.structured(
            slot="router/v1", variables=_ROUTER_VARS,
            response_model=RoutingDecision, model="flagship/x",
        )
    assert calls == ["flagship/x"]  # no downgrade attempt


async def test_structured_small_tier_call_raises_without_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call already on the small tier has no lower tier — it raises on
    failure rather than looping."""
    calls: list[str] = []

    async def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["model"])
        raise _timeout(kwargs["model"])

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake)

    adapter = LiteLlmAdapter(fallback_model="small/y")
    with pytest.raises(litellm.Timeout):
        await adapter.structured(
            slot="router/v1", variables=_ROUTER_VARS,
            response_model=RoutingDecision, model="small/y",  # already the fallback
        )
    assert calls == ["small/y"]


async def test_structured_downgrade_recorded_on_active_trace_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a downgrade happens inside a tracer step, the step's recorded result
    carries ``_model_fallbacks`` so the trace shows 'flagship → small'."""
    async def fake(**kwargs: Any) -> dict[str, Any]:
        if kwargs["model"] == "flagship/x":
            raise _timeout("flagship/x")
        return {"choices": [{"message": {"content": _VALID_DECISION}}]}

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "acompletion", fake)

    async with aiosqlite.connect(":memory:") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
        await conn.commit()
        tracer = Tracer(conn, run_id=1, branch="")
        adapter = LiteLlmAdapter(fallback_model="small/y")
        async with tracer.step(agent="report", tool="report:outline", model="flagship/x"):
            await adapter.structured(
                slot="router/v1", variables=_ROUTER_VARS,
                response_model=RoutingDecision, model="flagship/x",
            )
        async with conn.execute(
            "SELECT result_summary_json FROM tool_calls ORDER BY step_index DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] is not None
    fallbacks = json.loads(row[0])["_model_fallbacks"]
    assert fallbacks[0]["from"] == "flagship/x"
    assert fallbacks[0]["to"] == "small/y"
    assert "Timeout" in fallbacks[0]["reason"]


async def test_stream_downgrades_to_small_when_flagship_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flagship stream that fails before emitting any token downgrades to the
    small tier (mirrors structured)."""
    models_called: list[str] = []

    async def fake(**kwargs: Any):  # noqa: ANN003
        models_called.append(kwargs["model"])

        async def gen():
            if kwargs["model"] == "flagship/x":
                raise litellm.APIConnectionError(
                    message="down", model="flagship/x", llm_provider="gemini",
                )
                yield  # pragma: no cover — makes this an async generator
            for c in ["O", "K"]:
                yield _chunk(c)
        return gen()

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    monkeypatch.setattr(litellm, "acompletion", fake)

    adapter = LiteLlmAdapter(fallback_model="small/y")
    out = [t async for t in adapter.stream(
        slot="chitchat/v1", variables=_CHITCHAT_VARS, model="flagship/x",
    )]
    assert "".join(out) == "OK"
    assert "flagship/x" in models_called and "small/y" in models_called


def test_should_downgrade_classifies_errors() -> None:
    """Availability errors downgrade; permanent client errors do not."""
    assert _should_downgrade(_timeout("m")) is True
    assert _should_downgrade(
        litellm.APIConnectionError(message="x", model="m", llm_provider="g")
    ) is True
    assert _should_downgrade(
        litellm.AuthenticationError(message="x", model="m", llm_provider="g")
    ) is False
    assert _should_downgrade(
        litellm.BadRequestError(message="x", model="m", llm_provider="g")
    ) is False


async def test_structured_with_history_builds_correct_messages() -> None:
    """structured() with history produces a messages array of len 2 + len(history)."""
    history = [
        {"role": "user", "content": "1+1=?"},
        {"role": "assistant", "content": "1+1 is 2!"},
    ]
    adapter = LiteLlmAdapter()
    # Capture the messages that would be sent by patching _messages
    captured: list[list[dict[str, str]]] = []
    original_messages = adapter._messages  # noqa: SLF001

    def patched_messages(
        slot: str,
        variables: dict,
        hist: list | None = None,
    ) -> list[dict[str, str]]:
        result = original_messages(slot, variables, hist)
        captured.append(result)
        return result

    adapter._messages = patched_messages  # type: ignore[method-assign]  # noqa: SLF001

    await adapter.structured(
        slot="router/v1",
        variables={"user_message": "So what did I ask?", "enabled_refs_count": 0,
                   "slide_attached": False},
        response_model=RoutingDecision,
        model="gpt-4o-mini",
        history=history,
        mock_response='{"intent":"chitchat",'
                      '"confidence":0.9,"reasoning":"follow-up"}',
    )

    assert len(captured) == 1
    messages = captured[0]
    # system + 2 history turns + user = 4
    assert len(messages) == 2 + len(history)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "1+1=?"
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "user"
