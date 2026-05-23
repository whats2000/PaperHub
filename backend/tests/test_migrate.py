"""Tests for the apply_schema migration runner.

Covers idempotent column-add migrations. Each test uses an in-memory DB
via the `migrated_db` fixture (tmp_path-backed aiosqlite connection that
has already run apply_schema once).
"""
import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema


@pytest.mark.asyncio
async def test_paper_content_has_asset_status_column(
    migrated_db: aiosqlite.Connection,
) -> None:
    """apply_schema must add asset_status to paper_content."""
    async with migrated_db.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    assert "asset_status" in cols


@pytest.mark.asyncio
async def test_apply_schema_idempotent_for_asset_status(
    tmp_path: "pytest.TempdirFactory",
) -> None:
    """Running apply_schema twice on the same DB must not raise."""
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "idem.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        # Second call must be a no-op, not an error.
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(paper_content)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "asset_status" in cols
