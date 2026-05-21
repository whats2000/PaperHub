import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_memories_table_and_fts_exist(migrated_db: aiosqlite.Connection) -> None:
    async with migrated_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('memories','memories_fts')"
    ) as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert {"memories", "memories_fts"} <= names


@pytest.mark.asyncio
async def test_global_requires_null_session(migrated_db: aiosqlite.Connection) -> None:
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO memories (scope, session_id, content) VALUES ('global', 1, 'x')"
        )


@pytest.mark.asyncio
async def test_session_requires_session_id(migrated_db: aiosqlite.Connection) -> None:
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO memories (scope, session_id, content) VALUES ('session', NULL, 'x')"
        )


@pytest.mark.asyncio
async def test_fts_sync_on_insert(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES ('session', 1, 'flow matching survey')"
    )
    await migrated_db.commit()
    async with migrated_db.execute(
        "SELECT m.content FROM memories_fts f JOIN memories m ON m.id=f.rowid WHERE memories_fts MATCH 'flow'"
    ) as cur:
        rows = await cur.fetchall()
    assert rows and "flow matching" in rows[0][0]


@pytest.mark.asyncio
async def test_fts_sync_on_update_and_delete(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES ('session', 1, 'alpha topic')"
    )
    await migrated_db.commit()
    await migrated_db.execute("UPDATE memories SET content='beta topic' WHERE id=1")
    await migrated_db.commit()
    async with migrated_db.execute("SELECT m.id FROM memories_fts f JOIN memories m ON m.id=f.rowid WHERE memories_fts MATCH 'beta'") as cur:
        assert await cur.fetchall()
    async with migrated_db.execute("SELECT m.id FROM memories_fts f JOIN memories m ON m.id=f.rowid WHERE memories_fts MATCH 'alpha'") as cur:
        assert not await cur.fetchall()
    await migrated_db.execute("DELETE FROM memories WHERE id=1")
    await migrated_db.commit()
    async with migrated_db.execute("SELECT m.id FROM memories_fts f JOIN memories m ON m.id=f.rowid WHERE memories_fts MATCH 'beta'") as cur:
        assert not await cur.fetchall()


@pytest.mark.asyncio
async def test_cascade_delete_removes_memories_and_fts(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES ('session', 1, 'cascade test token')"
    )
    await migrated_db.commit()
    await migrated_db.execute("DELETE FROM chat_sessions WHERE id=1")
    await migrated_db.commit()
    async with migrated_db.execute("SELECT COUNT(*) FROM memories") as cur:
        assert (await cur.fetchone())[0] == 0
    async with migrated_db.execute(
        "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'cascade'"
    ) as cur:
        assert (await cur.fetchone())[0] == 0
