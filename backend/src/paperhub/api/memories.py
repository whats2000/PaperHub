"""Memories REST surface (SRS FR-11 — UI-driven memory curation).

UI-driven deterministic operations: GET (list), PATCH (edit content /
toggle status), DELETE (forget).  POST is intentionally absent — adds
happen via chat (the Memory Agent's MCP tools).

Connection idiom: mirrors papers.py exactly.  Each endpoint opens a
fresh DB connection via ``async with open_db(settings.db_path) as conn``
inside the handler body — ``open_db`` is an ``@asynccontextmanager``, NOT
a coroutine, so it must never be ``await``-ed outside of ``async with``.

Ownership rules mirror the MCP memory tools (edit_memory / forget_memory):
  * global memories → accessible from ANY session_id
  * session-scoped memories → only the owning session may mutate them

The ``X-Paperhub-Session-Id`` request header supplies the caller's
session context.  A missing or non-integer header is treated as ``None``
(unauthenticated / global-only write access).
"""
from __future__ import annotations

from typing import Any

import aiosqlite
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from paperhub.agents.memory_tools import (
    MemoryScopeError,
    _owned_or_raise,
    edit_memory,
    forget_memory,
)
from paperhub.config import load_settings
from paperhub.db.connection import open_db

router = APIRouter(prefix="/memories", tags=["memories"])


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class MemoryPatchBody(BaseModel):
    content: str | None = None
    status: str | None = None  # validated in the handler: 'active' | 'superseded'


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_session_id(raw: str | None) -> int | None:
    """Parse the ``X-Paperhub-Session-Id`` header value to ``int | None``.

    Returns ``None`` when the header is absent or cannot be parsed as an
    integer (so the caller is treated as having no session context, which
    grants access to global memories only).
    """
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """Map a ``SELECT id, scope, session_id, content, created_at,
    updated_at, status, supersedes, superseded_by`` row to a plain dict."""
    return {
        "id": row[0],
        "scope": row[1],
        "session_id": row[2],
        "content": row[3],
        "created_at": row[4],
        "updated_at": row[5],
        "status": row[6],
        "supersedes": row[7],
        "superseded_by": row[8],
    }


_SELECT_COLS = (
    "id, scope, session_id, content, created_at, updated_at, "
    "status, supersedes, superseded_by"
)


# ---------------------------------------------------------------------------
# GET /memories
# ---------------------------------------------------------------------------


@router.get("", response_model=list[dict[str, Any]])
async def list_memories(
    session_id: int = Query(..., ge=1),
) -> list[dict[str, Any]]:
    """List all memories visible to ``session_id``:

    * ALL global memories (both active and superseded)
    * ALL memories scoped to ``session_id`` (both active and superseded)

    Ordered by ``created_at DESC`` so the most-recent entries come first.
    """
    sql = (
        f"SELECT {_SELECT_COLS} FROM memories "
        "WHERE (scope = 'global') OR (scope = 'session' AND session_id = ?) "
        "ORDER BY created_at DESC"
    )
    settings = load_settings()
    async with open_db(settings.db_path) as conn, conn.execute(
        sql, (session_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# PATCH /memories/{memory_id}
# ---------------------------------------------------------------------------


@router.patch("/{memory_id}", response_model=dict[str, Any])
async def patch_memory(
    memory_id: int,
    body: MemoryPatchBody,
    x_paperhub_session_id: str | None = Header(None),
) -> dict[str, Any]:
    """Edit content and/or status of a memory row, ownership-checked.

    * ``content`` change → delegates to :func:`edit_memory` (handles
      ownership + FTS index update).
    * ``status`` change → validates the value, calls
      :func:`_owned_or_raise`, then issues a direct ``UPDATE``.

    Returns the updated row.  404 if the memory does not exist after the
    update (should not happen but guards against races).
    """
    session_id = _parse_session_id(x_paperhub_session_id)

    if body.content is None and body.status is None:
        raise HTTPException(
            status_code=422,
            detail="at least one of 'content' or 'status' must be provided",
        )

    if body.status is not None and body.status not in ("active", "superseded"):
        raise HTTPException(
            status_code=422,
            detail=f"status must be 'active' or 'superseded', got {body.status!r}",
        )

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        # --- content edit ---
        if body.content is not None:
            try:
                await edit_memory(
                    conn,
                    session_id=session_id,
                    memory_id=memory_id,
                    content=body.content,
                )
            except MemoryScopeError as exc:
                msg = str(exc)
                if "not found" in msg:
                    raise HTTPException(404, msg) from exc
                raise HTTPException(403, msg) from exc

        # --- status toggle ---
        if body.status is not None:
            try:
                await _owned_or_raise(
                    conn,
                    session_id=session_id,
                    memory_id=memory_id,
                )
            except MemoryScopeError as exc:
                msg = str(exc)
                if "not found" in msg:
                    raise HTTPException(404, msg) from exc
                raise HTTPException(403, msg) from exc

            cur = await conn.execute(
                "UPDATE memories SET status = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (body.status, memory_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, f"memory {memory_id} not found")
            await conn.commit()

        # Return the updated row.
        async with conn.execute(
            f"SELECT {_SELECT_COLS} FROM memories WHERE id = ?",
            (memory_id,),
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(404, f"memory {memory_id} not found")
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# DELETE /memories/{memory_id}
# ---------------------------------------------------------------------------


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: int,
    x_paperhub_session_id: str | None = Header(None),
) -> dict[str, bool]:
    """Forget a memory row, ownership-checked.

    Delegates to :func:`forget_memory`, which handles the ownership
    guard and the FTS index tombstone.  Returns ``{"ok": True}`` on
    success (mirroring the papers.py PATCH shape — 200 with a body
    rather than 204 so API callers can distinguish success from a
    silent network drop).
    """
    session_id = _parse_session_id(x_paperhub_session_id)
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        try:
            await forget_memory(
                conn,
                session_id=session_id,
                memory_id=memory_id,
            )
        except MemoryScopeError as exc:
            msg = str(exc)
            if "not found" in msg:
                raise HTTPException(404, msg) from exc
            raise HTTPException(403, msg) from exc
    return {"ok": True}


__all__ = ["router", "MemoryPatchBody"]
