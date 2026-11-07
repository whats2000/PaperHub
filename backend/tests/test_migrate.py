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


# ---------------------------------------------------------------------------
# v2.30: chat_sessions.forked_from_session_id (fork lineage) — the ALTER branch
# that fires on an EXISTING pre-v2.30 DB (the fresh-schema path is covered by
# schema.sql, which already has the column).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_adds_forked_from_to_existing_db(tmp_path: Path) -> None:
    """A DB created before v2.30 (chat_sessions WITHOUT the column) gets
    forked_from_session_id added by the idempotent ALTER branch, with the
    self-referential ON DELETE SET NULL FK present AND enforced."""
    db_path = tmp_path / "old.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        # Simulate a pre-v2.30 chat_sessions: no forked_from_session_id (and no
        # deleted_at — both column-add migrations should run).
        await conn.execute(
            "CREATE TABLE chat_sessions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            " title TEXT NOT NULL DEFAULT 'New chat')"
        )
        await conn.commit()

        await apply_schema(conn)  # the upgrade path (runs the ALTER)
        await apply_schema(conn)  # idempotent — must not raise

        async with conn.execute("PRAGMA table_info(chat_sessions)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        assert "forked_from_session_id" in cols

        # The self-FK carries ON DELETE SET NULL.
        async with conn.execute(
            "PRAGMA foreign_key_list(chat_sessions)"
        ) as cur:
            fks = await cur.fetchall()
        # row: (id, seq, table, from, to, on_update, on_delete, match)
        assert any(
            r[3] == "forked_from_session_id" and r[6].upper() == "SET NULL"
            for r in fks
        )

        # FK enforced: deleting the parent nulls the fork's lineage pointer.
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('parent')")
        await conn.execute(
            "INSERT INTO chat_sessions (title, forked_from_session_id) "
            "VALUES ('fork', 1)"
        )
        await conn.commit()
        await conn.execute("DELETE FROM chat_sessions WHERE id = 1")
        await conn.commit()
        async with conn.execute(
            "SELECT forked_from_session_id FROM chat_sessions WHERE id = 2"
        ) as cur:
            assert (await cur.fetchone())[0] is None


# ---------------------------------------------------------------------------
# F2.1 A2': chunks.page + chunks.bbox columns (Marker block provenance)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunks_has_page_and_bbox_columns(
    migrated_db: aiosqlite.Connection,
) -> None:
    """apply_schema must add page + bbox to chunks (F2.1 A2')."""
    async with migrated_db.execute("PRAGMA table_info(chunks)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    assert "page" in cols
    assert "bbox" in cols


@pytest.mark.asyncio
async def test_apply_schema_idempotent_for_page_and_bbox(
    tmp_path: Path,
) -> None:
    """Running apply_schema twice must not raise for chunks.page/bbox."""
    db_path = tmp_path / "idem_page_bbox.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        # Second call must be a no-op, not an error.
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(chunks)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "page" in cols
    assert "bbox" in cols


# ---------------------------------------------------------------------------
# F2.1 A3: paper_content.layout_json column (per-paper figure+table index)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_content_has_layout_json_column(
    migrated_db: aiosqlite.Connection,
) -> None:
    """apply_schema must add layout_json to paper_content (F2.1 A3)."""
    async with migrated_db.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    assert "layout_json" in cols


@pytest.mark.asyncio
async def test_apply_schema_idempotent_for_layout_json(
    tmp_path: Path,
) -> None:
    """Running apply_schema twice must not raise for paper_content.layout_json."""
    db_path = tmp_path / "idem_layout.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        # Second call must be a no-op, not an error.
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(paper_content)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "layout_json" in cols


# ---------------------------------------------------------------------------
# H0: paper_content.is_private column
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_content_has_is_private_column(
    migrated_db: aiosqlite.Connection,
) -> None:
    async with migrated_db.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1]: row for row in await cur.fetchall()}
    assert "is_private" in cols
    assert cols["is_private"][3] == 1  # NOT NULL
    assert cols["is_private"][4] == "0"  # DEFAULT 0


@pytest.mark.asyncio
async def test_apply_schema_idempotent_for_is_private(tmp_path: Path) -> None:
    db_path = tmp_path / "idem_private.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(paper_content)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert "is_private" in cols


@pytest.mark.asyncio
async def test_migration_adds_is_private_to_existing_db(tmp_path: Path) -> None:
    """A pre-H0 paper_content table (no is_private) gains the column, rows -> 0."""
    db_path = tmp_path / "old.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute("ALTER TABLE paper_content DROP COLUMN is_private")
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES ('arxiv:1', 'arxiv', '1', 't', 'a', 'b', 'c')"
        )
        await conn.commit()
        await apply_schema(conn)
        async with conn.execute("SELECT is_private FROM paper_content") as cur:
            row = await cur.fetchone()
        assert row is not None and row[0] == 0
        # The migrated column must carry the same CHECK as a fresh schema.
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute("UPDATE paper_content SET is_private = 2")
