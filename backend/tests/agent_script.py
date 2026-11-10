"""Scripted core-agent turns for POST /chat tests (H4 Task 10).

Since H4 every chat turn drives the core agent, whose model calls go through
``LiteLlmAdapter.tool_turn``. :func:`script_agent` replaces that method with
a script, so a test decides which tools the agent calls and what it answers
without a provider. :func:`pin_pipeline_turn` routes ``POST /chat`` to the
pre-H4 router turn for tests of pipeline-only behaviour.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from paperhub.llm.adapter import ToolCall, ToolTurn
from paperhub.llm.litellm_adapter import LiteLlmAdapter

ScriptItem = ToolTurn | BaseException | Callable[[dict[str, Any]], Awaitable[ToolTurn]]


def answer(text: str) -> ToolTurn:
    return ToolTurn(content=text)


def calls(*items: tuple[str, dict[str, Any]]) -> ToolTurn:
    return ToolTurn(content="", tool_calls=[
        ToolCall(id=f"call_{i}", name=n, arguments=a)
        for i, (n, a) in enumerate(items)
    ])


def script_agent(
    monkeypatch: pytest.MonkeyPatch, turns: list[ScriptItem],
    *, token_delay: float = 0.0,
) -> list[dict[str, Any]]:
    """Each ``tool_turn`` call pops the next item: a ``ToolTurn`` (its
    content streams through ``on_token`` in two pieces), an exception to
    raise, or an async callable given the call's kwargs. Returns the list
    of recorded kwargs."""
    seen: list[dict[str, Any]] = []

    async def fake(self: Any, **kw: Any) -> ToolTurn:
        seen.append(kw)
        item = turns.pop(0)
        if isinstance(item, BaseException):
            raise item
        turn = item if isinstance(item, ToolTurn) else await item(kw)
        on_token: Callable[[str], Awaitable[None]] | None = kw.get("on_token")
        if on_token is not None and turn.content:
            half = len(turn.content) // 2
            for piece in (turn.content[:half], turn.content[half:]):
                if token_delay:
                    await asyncio.sleep(token_delay)
                await on_token(piece)
        return turn

    monkeypatch.setattr(LiteLlmAdapter, "tool_turn", fake)
    return seen


def pin_pipeline_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route POST /chat to the pre-H4 router + intent pipeline turn."""
    import paperhub.api.chat as chat_module

    monkeypatch.setattr(chat_module, "run_agent", chat_module.run_pipeline_turn)
