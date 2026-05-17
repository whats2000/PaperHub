"""MCP client + scope-check dispatcher (Phase A stub).

The real stdio/socket dispatch to upstream MCP servers via the `mcp`
SDK lands in Task 6 (`arxiv`, `grobid`). The scope-check gate lives here
from day 1 so every later phase plugs into a single auditable validation
point.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from paperhub.mcp.scopes import McpInvocation, McpToolScope, ScopeRejection, check_scope


class McpScopeViolation(RuntimeError):
    def __init__(self, rejection: ScopeRejection, invocation: McpInvocation) -> None:
        super().__init__(rejection.reason)
        self.rejection = rejection
        self.invocation = invocation


McpDispatcher = Callable[[McpInvocation], Awaitable[dict[str, object]]]


class McpClient:
    def __init__(self, *, scopes: dict[str, McpToolScope], dispatcher: McpDispatcher) -> None:
        self._scopes = scopes
        self._dispatcher = dispatcher

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        scope = self._scopes.get(invocation.tool)
        if scope is None:
            raise McpScopeViolation(
                ScopeRejection(reason=f"no scope configured for tool {invocation.tool!r}"),
                invocation,
            )
        rejection = check_scope(invocation, scope)
        if rejection is not None:
            raise McpScopeViolation(rejection, invocation)
        return await self._dispatcher(invocation)
