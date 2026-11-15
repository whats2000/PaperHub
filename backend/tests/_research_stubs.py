"""Shared stubs for the research-pipeline / discoverer tests."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from paperhub.outbound.gate import OutboundGate
from paperhub.tracing.tracer import Tracer


def msg(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def async_completion_mock(seq: list[dict[str, Any]]) -> AsyncMock:
    return AsyncMock(side_effect=seq)


class StubRegistry:
    """In-memory MCP registry: stub web.search + papers.search_semantic_scholar."""

    def __init__(
        self,
        web_hits: list[dict[str, Any]] | None = None,
        ss_hits: list[dict[str, Any]] | None = None,
        has_web_search: bool = True,
    ) -> None:
        self.web_hits = web_hits or []
        self.ss_hits = ss_hits or []
        self._has_web = has_web_search
        self.call_log: list[tuple[str, dict[str, Any]]] = []

    async def has_tool(self, name: str) -> bool:
        if name == "web.search":
            return self._has_web
        return True

    async def aggregate_tool_schemas(self) -> list[dict[str, Any]]:
        if self._has_web:
            return [{
                "type": "function",
                "function": {
                    "name": "web.search",
                    "description": "Web search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }]
        return []

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.call_log.append((name, args))
        if name == "web.search":
            return list(self.web_hits)
        if name == "papers.search_semantic_scholar":
            return list(self.ss_hits)
        raise RuntimeError(f"StubRegistry: unknown tool {name!r}")


def dev_gate(tracer: Tracer) -> OutboundGate:
    """An outbound gate on the tracer's DB with no review model: with review
    off (the dev default) every query is sent unchanged and logged."""
    return OutboundGate(conn=tracer.connection, adapter=None,  # type: ignore[arg-type]
                        tracer=tracer, run_id=tracer.run_id)
