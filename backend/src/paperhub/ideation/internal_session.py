"""The one hidden chat session that holds ideation trace runs (Plan H3).

``chat_sessions.internal = 1`` keeps it out of ``GET /sessions``; the session
endpoints (delete/restore/fork) 404 on it; a partial unique index allows only
one such row; it has a row so the orphan-folder sweep never removes its folder.
"""
from __future__ import annotations

import aiosqlite

INTERNAL_TITLE = "Ideation (internal)"


async def _lookup(conn: aiosqlite.Connection) -> int | None:
    async with conn.execute(
        "SELECT id FROM chat_sessions WHERE internal = 1 ORDER BY id LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row is not None else None


async def ideation_session_id(conn: aiosqlite.Connection) -> int:
    sid = await _lookup(conn)
    if sid is None:
        # The unique index makes a concurrent creator a no-op; re-select wins.
        await conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (title, internal) VALUES (?, 1)",
            (INTERNAL_TITLE,),
        )
        await conn.commit()
        sid = await _lookup(conn)
        if sid is None:
            raise RuntimeError("ideation session creation failed")
    # Defensive: a tombstone would get it purged (cascading its trace runs).
    await conn.execute(
        "UPDATE chat_sessions SET deleted_at = NULL "
        "WHERE id = ? AND deleted_at IS NOT NULL",
        (sid,),
    )
    await conn.commit()
    return sid
