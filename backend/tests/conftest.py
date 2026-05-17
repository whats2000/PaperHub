from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest_asyncio

from paperhub.db.migrate import apply_schema


@pytest_asyncio.fixture
async def migrated_db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        yield conn
