"""MCP client + scope-check dispatcher (Phase A stub).

The real stdio/socket dispatch to upstream MCP servers via the `mcp`
SDK lands in Task 6 (`arxiv`, `grobid`). The scope-check gate lives here
from day 1 so every later phase plugs into a single auditable validation
point.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

from paperhub.mcp.scopes import McpInvocation, McpToolScope, ScopeRejection, check_scope

if TYPE_CHECKING:
    from paperhub.tracing.tracer import ToolCallTracer


class McpScopeViolation(RuntimeError):
    def __init__(self, rejection: ScopeRejection, invocation: McpInvocation) -> None:
        super().__init__(rejection.reason)
        self.rejection = rejection
        self.invocation = invocation


McpDispatcher = Callable[[McpInvocation], Awaitable[dict[str, object]]]


class McpClient:
    """Scope-checking MCP client.

    Parameters
    ----------
    scopes:
        Mapping of tool name → allowed scope (validated before dispatch).
    dispatcher:
        Async callable that performs the actual MCP invocation.
    tracer:
        Optional :class:`~paperhub.tracing.tracer.ToolCallTracer`. When
        provided a ``tool_calls`` row with ``status='rejected'`` is written
        *before* :exc:`McpScopeViolation` is raised (design §7 / I-1 fix).
    run_id:
        UUID for the current agent run (required when *tracer* is provided).
    step_index:
        Step counter within the run (required when *tracer* is provided).
    """

    def __init__(
        self,
        *,
        scopes: dict[str, McpToolScope],
        dispatcher: McpDispatcher,
        tracer: ToolCallTracer | None = None,
        run_id: UUID | None = None,
        step_index: int = 0,
    ) -> None:
        self._scopes = scopes
        self._dispatcher = dispatcher
        self._tracer = tracer
        self._run_id = run_id
        self._step_index = step_index

    def _record_rejection(self, invocation: McpInvocation, reason: str) -> None:
        """Write a rejected tool_calls row if a tracer is configured."""
        if self._tracer is None or self._run_id is None:
            return
        # Keep bytes/Path objects as their native types so redact() can see them
        # and convert them to placeholders. The tracer's json.dumps in
        # tracer.record will then receive a clean str-keyed dict with no binary
        # content.
        args_dict = invocation.args.model_dump()
        args_payload: dict[str, object] = {
            "tool": invocation.tool,
            "method": invocation.method,
            "args": args_dict,
        }
        self._tracer.record(
            run_id=self._run_id,
            step_index=self._step_index,
            parent_step=None,
            agent="mcp_client",
            tool=invocation.tool,
            model=None,
            args=args_payload,
            result_summary=None,
            latency_ms=0,
            token_in=None,
            token_out=None,
            status="rejected",
            error=reason,
        )

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        scope = self._scopes.get(invocation.tool)
        if scope is None:
            no_scope_rejection = ScopeRejection(
                reason=f"no scope configured for tool {invocation.tool!r}"
            )
            self._record_rejection(invocation, no_scope_rejection.reason)
            raise McpScopeViolation(no_scope_rejection, invocation)
        scope_rejection = check_scope(invocation, scope)
        if scope_rejection is not None:
            self._record_rejection(invocation, scope_rejection.reason)
            raise McpScopeViolation(scope_rejection, invocation)
        return await self._dispatcher(invocation)
