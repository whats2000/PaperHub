"""Tests for sl_read — deterministic read_section worker (F6.1-R)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """SQLite connection with schema applied and test papers + chunks seeded."""
    async with aiosqlite.connect(str(tmp_path / "t.db")) as c:
        await apply_schema(c)

        # Paper 73: has chunks in two sections
        await c.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, arxiv_id, title, abstract, sections_json, "
            " source_path, source_dir_path, html_path) "
            "VALUES (73, 'k73', 'arxiv', '2301.00001', "
            "'A Test Paper', 'Abstract text.', NULL, "
            "'/p', '/d', '/h')"
        )

        # Two chunks in "Method" section (ids 101, 102, with distinct char_start)
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (101, 73, 'Method', 0, 50, 'First method chunk.')"
        )
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (102, 73, 'Method', 10, 100, 'Second method chunk.')"
        )
        # One chunk in a different section — proves filtering
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (103, 73, 'Results', 200, 300, 'Results chunk — must NOT appear.')"
        )

        await c.commit()
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_known_section_returns_ordered_chunks(
    conn: aiosqlite.Connection,
) -> None:
    """read_section_chunks returns chunk_ids=[101, 102] ordered by char_start."""
    from paperhub.agents.sl_read import ReadResult, read_section_chunks

    result = await read_section_chunks(
        paper_content_id=73, section_name="Method", conn=conn
    )

    assert isinstance(result, ReadResult)
    assert result.chunk_ids == [101, 102]
    assert "First method chunk." in result.text
    assert "Second method chunk." in result.text
    # The "Results" chunk must NOT bleed through
    assert "Results chunk" not in result.text


@pytest.mark.asyncio
async def test_read_unknown_section_returns_empty(
    conn: aiosqlite.Connection,
) -> None:
    """Unknown section name → ReadResult(text='', chunk_ids=[])."""
    from paperhub.agents.sl_read import read_section_chunks

    result = await read_section_chunks(
        paper_content_id=73, section_name="NonExistentSection", conn=conn
    )

    assert result.text == ""
    assert result.chunk_ids == []


@pytest.mark.asyncio
async def test_read_unknown_paper_returns_empty(
    conn: aiosqlite.Connection,
) -> None:
    """Unknown paper_content_id → ReadResult(text='', chunk_ids=[])."""
    from paperhub.agents.sl_read import read_section_chunks

    result = await read_section_chunks(
        paper_content_id=9999, section_name="Method", conn=conn
    )

    assert result.text == ""
    assert result.chunk_ids == []


@pytest.mark.asyncio
async def test_read_cap_applied(tmp_path: Path) -> None:
    """Text is capped at _READ_TEXT_CAP characters even when chunks are large."""
    from paperhub.agents.sl_read import _READ_TEXT_CAP, read_section_chunks

    async with aiosqlite.connect(str(tmp_path / "cap.db")) as c:
        await apply_schema(c)

        await c.execute(
            "INSERT INTO paper_content "
            "(id, content_key, kind, arxiv_id, title, abstract, sections_json, "
            " source_path, source_dir_path, html_path) "
            "VALUES (1, 'ck1', 'arxiv', '2400.00001', 'Cap Paper', 'Abstract.', "
            "NULL, '/s', '/d', '/h')"
        )

        # A single chunk whose text length exceeds _READ_TEXT_CAP
        big_text = "x" * (_READ_TEXT_CAP + 1000)
        await c.execute(
            "INSERT INTO chunks (id, paper_content_id, section, char_start, char_end, text) "
            "VALUES (1, 1, 'BigSection', 0, ?, ?)",
            (len(big_text), big_text),
        )
        await c.commit()

        result = await read_section_chunks(
            paper_content_id=1, section_name="BigSection", conn=c
        )

    assert len(result.text) <= _READ_TEXT_CAP
    assert result.chunk_ids == [1]
