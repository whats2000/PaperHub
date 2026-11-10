from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolCall:
    """One model-requested tool invocation. ``arguments`` is the parsed JSON
    object; unparseable text is kept as ``{"_raw": "<text>"}``."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolTurn:
    """One assembled assistant turn: text, requested tool calls, and token
    usage (``prompt_tokens`` / ``completion_tokens`` / ``finish_reason`` where
    reported)."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    # Calls emitted without a tool name; never dispatched. Callers record them
    # in the trace result as {"dropped_calls": [...]}.
    dropped_calls: list[dict[str, Any]] = field(default_factory=list)


class LlmAdapter(Protocol):
    async def structured(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        response_model: type[T],
        model: str,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> T: ...

    def stream(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        model: str,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...

    async def tool_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        **kwargs: Any,
    ) -> ToolTurn: ...
