"""F4.5 §III-5.3 style resolver — session override → global memory → default.

Resolution priority on every initial_draft / replace_frame:
  1. slide_style_overrides row for this session (if present)
  2. memories row scope='global', status='active', metadata.kind='slide_style_global'
  3. literal slide_style_default.tex (paper2slides-plus verbatim default)

The slide agent's replace_preamble(persist=True) tool calls set_session_override.
The router's deterministic command intercepts call clear_session_override
("reset slide style") + promote_to_global ("remember this style for all future
chats") without spawning the agent.
"""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Literal

import aiosqlite

OverrideSource = Literal["user_request", "agent_inferred", "global_memory_projection"]


def _default_preamble() -> str:
    return (files("paperhub.agents") / "slide_style_default.tex").read_text(encoding="utf-8")


async def resolve_preamble(*, session_id: int, conn: aiosqlite.Connection) -> str:
    """Return the resolved Beamer preamble for this session.

    Priority: session override → global memory → default file.
    """
    # 1. Session override
    async with conn.execute(
        "SELECT preamble_tex FROM slide_style_overrides WHERE session_id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        return str(row[0])

    # 2. Global memory row keyed by metadata.kind='slide_style_global'
    async with conn.execute(
        "SELECT content FROM memories "
        "WHERE scope = 'global' AND status = 'active' "
        "  AND metadata IS NOT NULL "
        "  AND json_extract(metadata, '$.kind') = 'slide_style_global' "
        "ORDER BY updated_at DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        return str(row[0])

    # 3. Default
    return _default_preamble()


async def set_session_override(
    *,
    session_id: int,
    preamble_tex: str,
    source: OverrideSource,
    conn: aiosqlite.Connection,
) -> None:
    """Upsert the per-session preamble override."""
    await conn.execute(
        """
        INSERT INTO slide_style_overrides (session_id, preamble_tex, source)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            preamble_tex = excluded.preamble_tex,
            source = excluded.source,
            updated_at = datetime('now')
        """,
        (session_id, preamble_tex, source),
    )
    await conn.commit()


async def clear_session_override(*, session_id: int, conn: aiosqlite.Connection) -> bool:
    """Drop the per-session preamble override. Returns True iff a row existed."""
    cur = await conn.execute(
        "DELETE FROM slide_style_overrides WHERE session_id = ?", (session_id,)
    )
    await conn.commit()
    return (cur.rowcount or 0) > 0


async def promote_to_global(
    *, session_id: int, conn: aiosqlite.Connection
) -> bool:
    """Copy this session's override (if present) into a global slide_style_global
    memory row. Returns True iff promotion happened (no-op when no override exists).

    Supersedes any prior slide_style_global row by flipping its status (mirrors the
    v2.17 conflict-supersede pattern).
    """
    async with conn.execute(
        "SELECT preamble_tex FROM slide_style_overrides WHERE session_id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False
    preamble_tex = str(row[0])

    # Supersede any prior global slide-style row.
    await conn.execute(
        """
        UPDATE memories
           SET status = 'superseded',
               superseded_by = NULL,
               updated_at = datetime('now')
         WHERE scope = 'global'
           AND status = 'active'
           AND metadata IS NOT NULL
           AND json_extract(metadata, '$.kind') = 'slide_style_global'
        """
    )
    metadata = json.dumps({"kind": "slide_style_global"})
    await conn.execute(
        """
        INSERT INTO memories (scope, content, created_at, updated_at, status, metadata)
        VALUES ('global', ?, datetime('now'), datetime('now'), 'active', ?)
        """,
        (preamble_tex, metadata),
    )
    await conn.commit()
    return True
