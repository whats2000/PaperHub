"""Per-request context for the `paperhub-papers` FastMCP server (SRS v2.6,
Plan C Task v2.5-3).

The tool handlers in :mod:`paperhub.mcp.server` need the same per-request
resources the chat endpoint plumbs through the Research Agent — a live
:class:`~paperhub.tracing.tracer.Tracer`, an :class:`aiosqlite.Connection`,
the active ``session_id`` and ``run_id``. FastMCP's tool-call dispatch is
just an async callable, so we propagate this state through a
``contextvars.ContextVar`` set by:

* a Starlette middleware on the mounted sub-app (production path — reads
  the ``X-Paperhub-Session-Id`` / ``X-Paperhub-Run-Id`` headers from the
  parent FastAPI request scope, opens a per-call DB connection +
  :class:`Tracer`, sets the context, clears it on the way out);
* test fixtures (in-memory ClientSession + direct ``call_tool`` paths
  that bypass ASGI middleware) — they call :func:`set_request_context`
  directly with a hand-rolled :class:`PaperhubPapersRequestContext`.

Both paths read via :func:`current_request_context` from the tool handler.
Missing context surfaces as :class:`LookupError`, which the tool wrapper
translates into a clean MCP error rather than an opaque ``AttributeError``.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

import aiosqlite

from paperhub.tracing.tracer import Tracer

__all__ = [
    "PaperhubPapersRequestContext",
    "current_request_context",
    "require_request_context",
    "reset_request_context",
    "set_request_context",
]


@dataclass(frozen=True)
class PaperhubPapersRequestContext:
    """Per-MCP-call resources threaded into the tool handlers.

    Frozen so the same context can be safely shared between concurrent
    awaits inside one tool call (the dispatchers fan out HTTP / SQL
    independently).

    ``caller_supplied_run`` distinguishes the **loopback path** (the agent
    set ``X-Paperhub-Run-Id`` from its own active run) from the **external
    client path** (Claude Desktop / Cursor — the middleware auto-created a
    fresh run). On the loopback path the agent's outer ``tracer.step``
    already records this tool call, so the handler MUST skip its own inner
    wrap to avoid a UNIQUE-constraint collision on ``(run_id, branch,
    step_index)``. On the external path the handler's wrap is the only
    source of ``tool_calls`` rows, so it stays.
    """

    conn: aiosqlite.Connection
    session_id: int
    run_id: int
    tracer: Tracer
    caller_supplied_run: bool = False


_REQUEST_CONTEXT: ContextVar[PaperhubPapersRequestContext] = ContextVar(
    "paperhub_papers_request_context",
)


def set_request_context(
    ctx: PaperhubPapersRequestContext,
) -> Token[PaperhubPapersRequestContext]:
    """Set the per-request context for the duration of a single MCP call.

    Returns a :class:`Token` the caller must pass back to
    :func:`reset_request_context` (typically in a ``finally`` block) so the
    next request starts from a clean slate.
    """
    return _REQUEST_CONTEXT.set(ctx)


def reset_request_context(token: Token[PaperhubPapersRequestContext]) -> None:
    """Clear the per-request context. Counterpart to :func:`set_request_context`."""
    _REQUEST_CONTEXT.reset(token)


def current_request_context() -> PaperhubPapersRequestContext:
    """Return the active context. Raises :class:`LookupError` when unset.

    The tool wrapper in :mod:`paperhub.mcp.server` catches this and converts
    it into a structured MCP error so a misconfigured external client gets
    a useful diagnostic, not a 500.
    """
    return _REQUEST_CONTEXT.get()


def require_request_context() -> PaperhubPapersRequestContext:
    """Fetch the active per-call MCP context or raise a clean RuntimeError.

    Shared by every in-process FastMCP server (papers / sql / memory): the
    middleware sets it from the X-Paperhub-* headers; tests prime it via
    set_request_context(). Translates the bare LookupError into a diagnostic.
    """
    try:
        return current_request_context()
    except LookupError as exc:
        raise RuntimeError(
            "in-process MCP tool invoked without a request context "
            "(no X-Paperhub-Session-Id header, and no fixture primed the contextvar)"
        ) from exc
