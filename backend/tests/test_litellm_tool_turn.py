import json
from typing import Any

import aiosqlite
import litellm
import pytest

from paperhub.db.migrate import apply_schema
from paperhub.llm.adapter import ToolCall, ToolTurn
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer

TOOLS = [{"type": "function", "function": {"name": "read_section", "parameters": {}}}]


def _content(text: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": text}}]}


def _tc(index: int, *, id: str | None = None, name: str | None = None,
        args: str | None = None) -> dict[str, Any]:
    fn: dict[str, Any] = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    call: dict[str, Any] = {"index": index, "function": fn}
    if id is not None:
        call["id"] = id
    return {"choices": [{"delta": {"tool_calls": [call]}}]}


def _usage(p: int, c: int) -> dict[str, Any]:
    return {"choices": [], "usage": {"prompt_tokens": p, "completion_tokens": c}}


class _Fake:
    def __init__(self, *scripts: list[Any]) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        script = self.scripts.pop(0)

        async def gen() -> Any:
            for item in script:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return gen()


async def test_content_only_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake([_content("Hel"), _content("lo"), _usage(10, 2)])
    monkeypatch.setattr(litellm, "acompletion", fake)
    seen: list[str] = []

    async def on_token(t: str) -> None:
        seen.append(t)

    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[{"role": "user", "content": "hi"}], tools=TOOLS, model="m/x",
        on_token=on_token,
    )
    assert isinstance(turn, ToolTurn)
    assert turn.content == "Hello" and turn.tool_calls == []
    assert seen == ["Hel", "lo"]
    assert turn.usage == {"prompt_tokens": 10, "completion_tokens": 2}
    assert fake.calls[0]["stream"] is True
    assert fake.calls[0]["tool_choice"] == "auto"
    assert fake.calls[0]["tools"] == TOOLS
    assert fake.calls[0]["stream_options"] == {"include_usage": True}


async def test_parallel_tool_calls_assembled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake([
        _tc(0, id="call_a", name="read_section", args='{"sec'),
        _tc(1, id="call_b", name="list_sections", args=""),
        _tc(0, args='tion": "Intro"}'),
        _tc(1, args="{}"),
    ])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[], tools=TOOLS, model="m/x",
    )
    assert turn.tool_calls == [
        ToolCall(id="call_a", name="read_section", arguments={"section": "Intro"}),
        ToolCall(id="call_b", name="list_sections", arguments={}),
    ]


async def test_malformed_args_and_missing_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake([_tc(0, name="read_section", args='{"oops'), _tc(1, name="x", args="")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[], tools=TOOLS, model="m/x",
    )
    assert turn.tool_calls[0].arguments == {"_raw": '{"oops'}
    assert turn.tool_calls[0].id == "call_0"
    assert turn.tool_calls[1].arguments == {} and turn.tool_calls[1].id == "call_1"


async def test_transient_retry_then_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nosleep(_: float) -> None:
        return None

    monkeypatch.setattr("paperhub.llm.litellm_adapter.asyncio.sleep", nosleep)
    boom = RuntimeError("Server disconnected")
    dead = litellm.exceptions.Timeout("t", "m/big", "p")
    fake = _Fake([boom], [dead], [_content("ok")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="m/small").tool_turn(
        messages=[], tools=TOOLS, model="m/big",
    )
    assert turn.content == "ok"
    assert [c["model"] for c in fake.calls] == ["m/big", "m/big", "m/small"]


async def test_midstream_failure_after_token_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Fake([_content("a"), RuntimeError("Server disconnected")])
    monkeypatch.setattr(litellm, "acompletion", fake)

    async def on_token(_: str) -> None:
        return None

    with pytest.raises(RuntimeError):
        await LiteLlmAdapter(fallback_model="m/small").tool_turn(
            messages=[], tools=TOOLS, model="m/big", on_token=on_token,
        )
    assert len(fake.calls) == 1


async def test_usage_noted_on_trace_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(litellm, "acompletion", _Fake([_content("x"), _usage(7, 3)]))
    async with aiosqlite.connect(":memory:") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute("INSERT INTO runs (session_id) VALUES (1)")
        await conn.commit()
        tracer = Tracer(conn, run_id=1, branch="")
        async with tracer.step(agent="agent", tool="agent:step", model="m/x") as step:
            await LiteLlmAdapter(fallback_model="").tool_turn(
                messages=[], tools=TOOLS, model="m/x",
            )
            step.record_result({"ok": 1})
        async with conn.execute(
            "SELECT result_summary_json, token_in, token_out FROM tool_calls"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    usage = json.loads(row[0])["_llm_usage"]
    assert usage[0]["prompt_tokens"] == 7 and usage[0]["completion_tokens"] == 3
    assert (row[1], row[2]) == (7, 3)


async def test_repeated_full_name_not_concatenated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Fake([
        _tc(0, id="c1", name="read_section", args='{"a"'),
        _tc(0, name="read_section", args=": 1}"),
    ])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[], tools=TOOLS, model="m/x",
    )
    assert turn.tool_calls == [ToolCall("c1", "read_section", {"a": 1})]


async def test_separate_chunks_without_index_make_separate_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Fake([
        _tc(None, id="c1", name="a", args="{}"),  # type: ignore[arg-type]
        _tc(None, id="c2", name="b", args='{"x": 1}'),  # type: ignore[arg-type]
    ])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[], tools=TOOLS, model="m/x",
    )
    assert turn.tool_calls == [ToolCall("c1", "a", {}), ToolCall("c2", "b", {"x": 1})]


async def test_empty_tools_omits_tool_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake([_content("x")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    await LiteLlmAdapter(fallback_model="").tool_turn(messages=[], tools=[], model="m/x")
    assert "tools" not in fake.calls[0] and "tool_choice" not in fake.calls[0]


async def test_unnamed_call_dropped_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake([_tc(0, id="c1", args='{"q": 1}'), _tc(1, id="c2", name="ok", args="{}")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[], tools=TOOLS, model="m/x",
    )
    assert turn.tool_calls == [ToolCall("c2", "ok", {})]
    assert turn.dropped_calls == [{"id": "c1", "arguments": {"q": 1}}]


async def test_non_dict_json_args_kept_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake([_tc(0, id="c1", name="x", args="[1]")])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[], tools=TOOLS, model="m/x",
    )
    assert turn.tool_calls[0].arguments == {"_raw": "[1]"}


async def test_finish_reason_reported_in_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty turn must say why it ended (stop / length / safety / ...) so
    the agent loop's trace can tell an empty answer's cause."""
    fake = _Fake([{"choices": [{"delta": {}, "finish_reason": "stop"}]}, _usage(9, 0)])
    monkeypatch.setattr(litellm, "acompletion", fake)
    turn = await LiteLlmAdapter(fallback_model="").tool_turn(
        messages=[{"role": "user", "content": "hi"}], tools=TOOLS, model="m/x",
    )
    assert turn.content == "" and turn.tool_calls == []
    assert turn.usage == {"prompt_tokens": 9, "completion_tokens": 0,
                          "finish_reason": "stop"}
