# backend/tests/test_settings_schema.py
import aiosqlite
import pytest

pytestmark = pytest.mark.asyncio


async def test_settings_table_exists(migrated_db: aiosqlite.Connection) -> None:
    async with migrated_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
    ) as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert "settings" in names


async def test_settings_key_is_primary_key(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute(
        "INSERT INTO settings (key, value) VALUES ('PAPERHUB_LOG_LEVEL', 'DEBUG')"
    )
    # Second insert of the same key must conflict (PK).
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO settings (key, value) VALUES ('PAPERHUB_LOG_LEVEL', 'INFO')"
        )
