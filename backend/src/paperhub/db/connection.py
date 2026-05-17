from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


@asynccontextmanager
async def open_db(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn
