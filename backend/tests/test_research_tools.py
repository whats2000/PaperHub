"""Tests for research_tools dispatchers (SRS v2.3, FR-07)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from paperhub.agents.research_tools import (
    add_paper_to_session_dispatch,
    search_library_dispatch,
)
from paperhub.pipelines.paper_pipeline import (
    IngestRequest,
    IngestResult,
    PaperPipeline,
)

pytestmark = pytest.mark.asyncio


async def _insert_paper_content(
    conn: aiosqlite.Connection,
    *,
    arxiv_id: str,
    title: str,
    abstract: str,
) -> int:
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"arxiv:{arxiv_id}",
            "arxiv",
            arxiv_id,
            title,
            "[]",
            2024,
            abstract,
            "/tmp/x.tex",
            "/tmp",
            "/tmp/x.html",
        ),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _make_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_search_library_excludes_session_attached_rows(
    migrated_db: aiosqlite.Connection,
) -> None:
    """search_library_dispatch must NOT return paper_content rows already
    in the given session."""
    session_id = await _make_session(migrated_db)
    pcid_a = await _insert_paper_content(
        migrated_db,
        arxiv_id="2401.00001",
        title="Transformer Attention",
        abstract="self-attention mechanism",
    )
    pcid_b = await _insert_paper_content(
        migrated_db,
        arxiv_id="2401.00002",
        title="Another Transformer Paper",
        abstract="more attention",
    )
    # Attach A to the session — should be filtered out.
    await migrated_db.execute(
        "INSERT INTO papers (session_id, paper_content_id) VALUES (?, ?)",
        (session_id, pcid_a),
    )
    await migrated_db.commit()

    hits = await search_library_dispatch(
        query="transformer",
        conn=migrated_db,
        session_id=session_id,
    )
    ids = {h.paper_content_id for h in hits}
    assert pcid_a not in ids
    assert pcid_b in ids


async def test_add_paper_library_is_idempotent(
    migrated_db: aiosqlite.Connection,
) -> None:
    """Calling add_paper_to_session_dispatch twice with the same library:<id>
    must not create a duplicate papers row (UNIQUE constraint)."""
    session_id = await _make_session(migrated_db)
    pcid = await _insert_paper_content(
        migrated_db, arxiv_id="2401.00099",
        title="Test Paper", abstract="abs",
    )
    pipeline = MagicMock(spec=PaperPipeline)

    r1 = await add_paper_to_session_dispatch(
        paper_id=f"library:{pcid}",
        reason="test reason",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
    )
    r2 = await add_paper_to_session_dispatch(
        paper_id=f"library:{pcid}",
        reason="test reason again",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
    )
    assert r1.paper_content_id == r2.paper_content_id == pcid
    assert r1.papers_id == r2.papers_id
    assert r1.cache_hit is True
    assert r1.title == "Test Paper"

    async with migrated_db.execute(
        "SELECT COUNT(*) FROM papers WHERE session_id = ? AND paper_content_id = ?",
        (session_id, pcid),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 1


async def test_add_paper_arxiv_calls_pipeline_ingest(
    migrated_db: aiosqlite.Connection,
) -> None:
    """arxiv:<id> path delegates to PaperPipeline.ingest with the right
    IngestRequest shape."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)
    pipeline.ingest = AsyncMock(
        return_value=IngestResult(
            paper_content_id=77,
            papers_id=88,
            cache_hit=False,
            title="Stub Paper",
        ),
    )

    result = await add_paper_to_session_dispatch(
        paper_id="arxiv:2403.12345",
        reason="best match",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
    )

    pipeline.ingest.assert_awaited_once()
    call_args = pipeline.ingest.await_args
    assert call_args is not None
    sent: IngestRequest = call_args.args[0]
    assert isinstance(sent, IngestRequest)
    assert sent.session_id == session_id
    assert sent.arxiv_id == "2403.12345"

    assert result.paper_content_id == 77
    assert result.papers_id == 88
    assert result.cache_hit is False
    assert result.title == "Stub Paper"


async def test_add_paper_unrecognised_prefix_raises(
    migrated_db: aiosqlite.Connection,
) -> None:
    pipeline = MagicMock(spec=PaperPipeline)
    with pytest.raises(ValueError, match="unrecognised paper_id prefix"):
        await add_paper_to_session_dispatch(
            paper_id="garbage:1",
            reason="x",
            pipeline=pipeline,
            conn=migrated_db,
            session_id=1,
        )
