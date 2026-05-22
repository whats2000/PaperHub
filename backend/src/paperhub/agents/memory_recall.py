"""Recall-injection helper (SRS v2.16 FR-10). FTS top-k -> labeled context block
injected into paper_qa / library_stats prompts. On by default; semantic recall
is an env-flagged upgrade path (not implemented here)."""
from __future__ import annotations

import aiosqlite

from paperhub.agents.memory_tools import recall_memories

_HEADER = "Relevant remembered facts (use if helpful, ignore if not):"
_ACTIVE_HEADER = "Active remembered facts / standing preferences:"


async def build_memory_context_block(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    query: str,
    enabled: bool = True,
    limit: int = 5,
) -> str:
    """Return a formatted context block from recalled memories.

    Returns an empty string when disabled, when the query tokenises to
    nothing, or when no memories match.  The block is designed to be
    appended to the USER section of any prompt — an empty string renders
    harmlessly.
    """
    if not enabled:
        return ""
    hits = await recall_memories(
        conn, session_id=session_id, query=query, scope="both", limit=limit,
    )
    if not hits:
        return ""
    lines = "\n".join(f"- ({h.scope}) {h.content}" for h in hits)
    return f"{_HEADER}\n{lines}"


async def build_active_memory_block(
    conn: aiosqlite.Connection,
    *,
    session_id: int | None,
    limit: int = 20,
) -> str:
    """Return a formatted block of ALL active memories visible to the caller.

    Unlike :func:`build_memory_context_block` (which is FTS-keyed on the
    current query), this fetches active memories UNCONDITIONALLY — so a
    standing directive like "always respond in Japanese" is always surfaced
    even when the user's current message shares no tokens with it.  Used by
    the router to resolve ``response_language`` against a language preference,
    which then propagates to every downstream final-response prompt.

    Returns an empty string when there are no active memories.
    """
    if session_id is None:
        sql = (
            "SELECT scope, content FROM memories "
            "WHERE status = 'active' AND scope = 'global' "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params: tuple[object, ...] = (limit,)
    else:
        sql = (
            "SELECT scope, content FROM memories "
            "WHERE status = 'active' AND "
            "(scope = 'global' OR (scope = 'session' AND session_id = ?)) "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params = (session_id, limit)
    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    if not rows:
        return ""
    lines = "\n".join(f"- ({r[0]}) {r[1]}" for r in rows)
    return f"{_ACTIVE_HEADER}\n{lines}"
