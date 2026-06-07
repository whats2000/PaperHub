"""Tests for paperhub-reingest CLI (Plan C v2.10-5).

Exercises _reingest_one() in isolation against a tmp-path SQLite DB. The CLI
re-chunks paper_content rows and rewrites the SQLite chunks (no embeddings /
vectors).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

_FIXTURE_TEX = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample" / "main.tex"


def _fixture_copy(tmp_path: Path) -> Path:
    """Copy the arxiv_sample main.tex into a tmp dir and return the copy.

    reingest now rewrites ``source.flattened.tex`` next to the source on a
    non-dry-run re-extract; pointing it at the committed fixture would pollute
    the repo, so tests that mutate must work on a throwaway copy.
    """
    dest = tmp_path / "main.tex"
    shutil.copy(_FIXTURE_TEX, dest)
    return dest


@pytest_asyncio.fixture
async def test_db(tmp_path: Path) -> aiosqlite.Connection:
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA foreign_keys = ON")
    await apply_schema(conn)
    return conn


_SEED_COUNTER = 0


async def _seed_paper_content_row(
    conn: aiosqlite.Connection,
    source_path: str,
) -> int:
    """Insert a minimal paper_content row and return its id.

    Uses a counter so repeated calls get unique content_key values (arxiv_id
    is the discriminator in the CHECK constraint that requires exactly one of
    arxiv_id/sha256 to be non-NULL).
    """
    global _SEED_COUNTER
    _SEED_COUNTER += 1
    content_key = f"arxiv:test-fixture-{_SEED_COUNTER}"
    arxiv_id = f"test-{_SEED_COUNTER}"
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            content_key,
            "arxiv",
            arxiv_id,
            "Test Paper",
            "[]",
            "Test abstract.",
            source_path,
            str(Path(source_path).parent),
            str(Path(source_path).with_suffix(".html")),
        ),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _insert_bogus_chunk(conn: aiosqlite.Connection, pcid: int) -> int:
    """Insert one garbage chunk row (like the pre-v2.10-1 chunker produced)."""
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
        "VALUES (?, ?, ?, ?, ?)",
        (pcid, None, 0, 1, "x"),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _count_chunks(conn: aiosqlite.Connection, pcid: int) -> int:
    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _get_sections_json(conn: aiosqlite.Connection, pcid: int) -> Any:
    async with conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reingest_one_replaces_chunks_and_populates_sections_json(
    test_db: aiosqlite.Connection,
    tmp_path: Path,
) -> None:
    """Re-ingesting a row removes old chunks, inserts sane new chunks, and
    populates sections_json. paper_content.id must be unchanged."""
    import paperhub.cli.reingest as reingest_mod

    # Seed: one paper_content row + one bogus chunk. Use a tmp copy so the
    # non-dry-run reingest's source.flattened.tex write can't touch the fixture.
    pcid = await _seed_paper_content_row(test_db, str(_fixture_copy(tmp_path)))
    old_chunk_id = await _insert_bogus_chunk(test_db, pcid)
    assert await _count_chunks(test_db, pcid) == 1

    before, after = await reingest_mod._reingest_one(
        pcid, conn=test_db, dry_run=False
    )

    # (a) old chunk gone — old id must not exist
    async with test_db.execute(
        "SELECT id FROM chunks WHERE id = ?", (old_chunk_id,)
    ) as cur:
        assert await cur.fetchone() is None, "old chunk row must be deleted"

    # (b) new chunks present with sane (>1 char) lengths
    new_count = await _count_chunks(test_db, pcid)
    assert new_count > 0, "at least one new chunk expected"
    async with test_db.execute(
        "SELECT text FROM chunks WHERE paper_content_id = ?", (pcid,)
    ) as cur:
        rows = await cur.fetchall()
    for (text,) in rows:
        assert len(text) > 5, f"chunk text too short: {text!r}"

    # (c) sections_json populated and valid JSON list
    sj_raw = await _get_sections_json(test_db, pcid)
    assert sj_raw is not None, "sections_json must be populated"
    sections = json.loads(sj_raw)
    assert isinstance(sections, list)
    assert len(sections) > 0, "expected at least one named section"
    # Each entry has required keys
    for entry in sections:
        assert "name" in entry
        assert "char_start" in entry
        assert "char_end" in entry
        assert "token_count" in entry
        assert "chunk_count" in entry

    # (d) paper_content.id preserved
    async with test_db.execute(
        "SELECT id FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        preserved = await cur.fetchone()
    assert preserved is not None
    assert int(preserved[0]) == pcid

    # Return values make sense
    assert before == 1
    assert after == new_count


@pytest.mark.asyncio
async def test_reingest_one_skips_missing_source_file(
    test_db: aiosqlite.Connection,
    tmp_path: Path,
) -> None:
    """When source_path points at a nonexistent file, _reingest_one should
    skip without modifying the DB and return (0, 0)."""
    import paperhub.cli.reingest as reingest_mod

    missing_path = str(tmp_path / "does_not_exist.tex")
    pcid = await _seed_paper_content_row(test_db, missing_path)
    bogus_chunk_id = await _insert_bogus_chunk(test_db, pcid)

    before, after = await reingest_mod._reingest_one(
        pcid, conn=test_db, dry_run=False
    )

    assert (before, after) == (0, 0), "should return (0, 0) for missing file"

    # DB unchanged — bogus chunk still present
    async with test_db.execute(
        "SELECT id FROM chunks WHERE id = ?", (bogus_chunk_id,)
    ) as cur:
        still_there = await cur.fetchone()
    assert still_there is not None, "bogus chunk must not be deleted when source is missing"

    # sections_json still NULL
    sj = await _get_sections_json(test_db, pcid)
    assert sj is None, "sections_json must remain NULL when skipped"


@pytest.mark.asyncio
async def test_reingest_one_dry_run_does_not_mutate(
    test_db: aiosqlite.Connection,
) -> None:
    """dry_run=True must not change DB state."""
    import paperhub.cli.reingest as reingest_mod

    pcid = await _seed_paper_content_row(test_db, str(_FIXTURE_TEX))
    await _insert_bogus_chunk(test_db, pcid)

    before, after = await reingest_mod._reingest_one(
        pcid, conn=test_db, dry_run=True
    )

    # DB still has the original 1 bogus chunk
    db_count_after = await _count_chunks(test_db, pcid)
    assert db_count_after == 1, "dry_run must not delete/insert chunks"

    # sections_json still NULL
    sj = await _get_sections_json(test_db, pcid)
    assert sj is None, "dry_run must not update sections_json"

    # before=1 (the bogus chunk), after>0 (what *would* be inserted)
    assert before == 1
    assert after > 0
