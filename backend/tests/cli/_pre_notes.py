"""A workspace DB from before the notes layer, for the operator CLIs' tests."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema

# The notes layer's tables (Plan H5b.1), dropped to model a workspace made
# before this branch: the operator upgrade script runs before the new backend
# has ever started on it.
_NOTES_TABLES = (
    "note_blocks_fts", "note_exports", "note_comment_items", "note_comments",
    "mark_colors", "note_marks", "note_agent_block_reads", "note_agent_reads",
    "note_suggestions", "note_change_sets", "note_revisions",
    "note_block_history", "note_blocks", "notes",
)


@pytest.fixture
async def pre_notes_db(tmp_path: Path) -> Path:
    """A workspace DB with today's schema minus every notes table, plus one
    document and one idea whose free-text note holds ``"Old thought."``."""
    db = tmp_path / "pre.db"
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.execute("PRAGMA foreign_keys = OFF")
        for name in _NOTES_TABLES:
            await conn.execute(f"DROP TABLE IF EXISTS {name}")
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
            "source_dir_path, html_path) VALUES ('pdf:1', 'pdf_upload', ?, 'D', '', '', '')",
            ("1" * 64,))
        await conn.execute(
            "INSERT INTO ideas (id, source_doc, source_section, title, note) "
            "VALUES (1, 1, 'S', 'idea 1', 'Old thought.')")
        await conn.commit()
    return db


async def table_names(db: Path) -> set[str]:
    async with aiosqlite.connect(db) as conn, conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ) as cur:
        return {str(r[0]) for r in await cur.fetchall()}
