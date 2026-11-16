from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest_asyncio

from paperhub.db.migrate import apply_schema


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(tmp_path / "notes.db")) as c:
        await c.execute("PRAGMA foreign_keys = ON")
        await apply_schema(c)
        yield c
