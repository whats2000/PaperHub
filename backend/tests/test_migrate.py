"""Tests for the apply_schema migration runner.

Covers idempotent column-add migrations. Each test uses an in-memory DB
via the `migrated_db` fixture (tmp_path-backed aiosqlite connection that
has already run apply_schema once).
"""
from pathlib import Path

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
    db_path = Path(str(tmp_path)) / "idem.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        # Second call must be a no-op, not an error.
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(paper_content)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "asset_status" in cols


# ---------------------------------------------------------------------------
# A1: chunks.match_text column
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunks_has_match_text_column(
    migrated_db: aiosqlite.Connection,
) -> None:
    """apply_schema must add match_text to chunks (F2.1 A1)."""
    async with migrated_db.execute("PRAGMA table_info(chunks)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    assert "match_text" in cols


@pytest.mark.asyncio
async def test_apply_schema_idempotent_for_match_text(
    tmp_path: Path,
) -> None:
    """Running apply_schema twice must not raise for chunks.match_text."""
    db_path = tmp_path / "idem_match.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        # Second call must be a no-op, not an error.
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(chunks)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "match_text" in cols
