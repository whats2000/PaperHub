from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


async def configure_connection(conn: aiosqlite.Connection) -> None:
    """Apply the standard PRAGMAs every PaperHub connection needs.

    WAL lets concurrent readers run alongside a single writer with no
    reader/writer blocking, and busy_timeout makes a writer wait for a
    lock (up to 5s) instead of raising `database is locked` immediately —
    both load-bearing now that a long-lived background Marker worker
    writes concurrently with per-request connections.
    """
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA busy_timeout = 5000")


@asynccontextmanager
async def open_db(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(db_path) as conn:
        await configure_connection(conn)
        yield conn
