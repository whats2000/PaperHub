"""Shared problem creation + start, used by the Ideas API and the agent tool."""
from __future__ import annotations

from collections.abc import Callable

import aiosqlite

from paperhub.db.connection import write_transaction


async def insert_problem(conn: aiosqlite.Connection, *, text: str, tried: str = "") -> int:
    """Insert a ``generating`` problem row and return its id."""
    async with write_transaction(conn):
        cur = await conn.execute(
            "INSERT INTO problems (text, tried, status) VALUES (?, ?, 'generating')",
            (text, tried),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


async def start_problem_run(
    conn: aiosqlite.Connection, problem_id: int, start: Callable[[], object],
) -> None:
    """Run ``start`` (spawns the background run); on failure mark the problem
    ``error`` (so the UI offers Retry instead of polling forever) and re-raise."""
    try:
        start()
    except Exception:
        async with write_transaction(conn):
            await conn.execute(
                "UPDATE problems SET status = 'error' WHERE id = ?", (problem_id,))
        raise
