"""Recall-injection helper (SRS v2.16 FR-10). FTS top-k -> labeled context block
injected into paper_qa / library_stats prompts. On by default; semantic recall
is an env-flagged upgrade path (not implemented here)."""
from __future__ import annotations

import aiosqlite

from paperhub.agents.memory_tools import recall_memories

_HEADER = "Relevant remembered facts (use if helpful, ignore if not):"


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
