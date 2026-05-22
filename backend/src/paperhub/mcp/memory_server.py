"""In-process write-capable `memory` FastMCP server (SRS v2.16, Plan E Task 10).

Tools (namespace ``memory.*``):
  * recall(query, scope)         -> list[{id, scope, session_id, content, ...}]
  * add(content, scope)          -> {id: int}
  * edit(memory_id, content)     -> {ok: True}  or  {error: "rejected", reason: ...}
  * forget(memory_id)            -> {ok: True}  or  {error: "rejected", reason: ...}

This is the ONLY write MCP surface in PaperHub. Scope enforcement (NFR-05)
is handled deterministically by the :mod:`paperhub.agents.memory_tools`
dispatchers — :class:`~paperhub.agents.memory_tools.MemoryScopeError` is
converted into ``{"error": "rejected", "reason": ...}`` here so the calling
agent can mark its tracer step status='rejected' without raising.

Per the no-tracer-on-loopback convention (same as sql_server.py): handlers
do NOT write tracer steps — the agent's outer tracer step already records
this call on the loopback path, and the middleware handles the external path.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from paperhub.agents.memory_gate import MemoryGateRefusal
from paperhub.agents.memory_tools import (
    MemoryScopeError,
    RecallScope,
    Scope,
    add_memory,
    edit_memory,
    forget_memory,
    recall_memories,
)
from paperhub.mcp.server_context import require_request_context

__all__ = [
    "MEMORY_SERVER_NAME",
    "_add_handler",
    "_edit_handler",
    "_forget_handler",
    "_recall_handler",
    "build_paperhub_memory_server",
]

MEMORY_SERVER_NAME = "memory"


async def _recall_handler(query: str, scope: RecallScope = "both") -> list[dict[str, Any]]:
    """Full-text search over memories visible to the current session.

    ``scope='both'`` (default) returns global memories plus this session's
    memories. ``scope='session'`` limits to the current session only.
    ``scope='global'`` limits to global memories only.
    """
    ctx = require_request_context()
    hits = await recall_memories(ctx.conn, session_id=ctx.session_id, query=query, scope=scope)
    return [asdict(h) for h in hits]


async def _add_handler(content: str, scope: Scope) -> dict[str, Any]:
    """Persist a new memory for the current session or globally.

    ``scope='session'`` ties the memory to the active session (other sessions
    cannot see or modify it).  ``scope='global'`` is visible from all sessions.
    Returns ``{"id": <int>}`` on success or ``{"error": "rejected", ...}``
    when the scope/ownership contract is violated.
    """
    ctx = require_request_context()
    try:
        mid = await add_memory(ctx.conn, session_id=ctx.session_id, content=content, scope=scope)
    except (MemoryScopeError, MemoryGateRefusal) as exc:
        return {"error": "rejected", "reason": str(exc)}
    return {"id": mid}


async def _edit_handler(memory_id: int, content: str) -> dict[str, Any]:
    """Replace the content of an existing memory.

    Returns ``{"ok": True}`` on success, or ``{"error": "rejected", "reason": ...}``
    if the memory belongs to a different session (scope guard).
    """
    ctx = require_request_context()
    try:
        await edit_memory(
            ctx.conn, session_id=ctx.session_id, memory_id=memory_id, content=content
        )
    except MemoryScopeError as exc:
        return {"error": "rejected", "reason": str(exc)}
    return {"ok": True}


async def _forget_handler(memory_id: int) -> dict[str, Any]:
    """Delete a memory by id.

    Returns ``{"ok": True}`` on success, or ``{"error": "rejected", "reason": ...}``
    if the memory belongs to a different session (scope guard).
    """
    ctx = require_request_context()
    try:
        await forget_memory(ctx.conn, session_id=ctx.session_id, memory_id=memory_id)
    except MemoryScopeError as exc:
        return {"error": "rejected", "reason": str(exc)}
    return {"ok": True}


def build_paperhub_memory_server() -> FastMCP:
    """Construct the write-capable FastMCP memory server.

    The server's ``streamable_http_path`` is set to ``/`` so mounting at
    ``/mcp-memory`` (via :func:`~paperhub.mcp.mounting.mount_inprocess_mcp`)
    makes ``POST /mcp-memory`` the streamable-HTTP transport endpoint —
    matching the convention every other in-process MCP server uses.
    """
    server = FastMCP(MEMORY_SERVER_NAME, streamable_http_path="/")
    server.settings.json_response = True
    server.settings.stateless_http = True
    server.add_tool(
        _recall_handler,
        name="recall",
        description=(
            "Full-text search over memories visible to the current session. "
            "scope='both' (default) returns global + this session's memories. "
            "Returns a list of memory objects with id, scope, session_id, content, "
            "created_at, updated_at fields."
        ),
    )
    server.add_tool(
        _add_handler,
        name="add",
        description=(
            "Persist a new memory. scope='session' ties it to the active session "
            "(other sessions cannot see it); scope='global' is visible from all sessions. "
            "Returns {id: int} on success or {error: 'rejected', reason: ...} on violation."
        ),
    )
    server.add_tool(
        _edit_handler,
        name="edit",
        description=(
            "Replace the content of an existing memory by id. "
            "Returns {ok: true} or {error: 'rejected', reason: ...} if the memory "
            "belongs to a different session."
        ),
    )
    server.add_tool(
        _forget_handler,
        name="forget",
        description=(
            "Delete a memory by id. "
            "Returns {ok: true} or {error: 'rejected', reason: ...} if the memory "
            "belongs to a different session."
        ),
    )
    return server
