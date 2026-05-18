"""Tests for paper_pipeline.py — cache-aware orchestrator (SRS §III-5.1)."""
from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio

from paperhub.pipelines.arxiv_client import ArxivResult
from paperhub.pipelines.paper_pipeline import (
    IngestRequest,
    PaperPipeline,
    compute_content_key,
)
from paperhub.rag.chroma import ChromaStore

# ---------------------------------------------------------------------------
# Fixture: arxiv_sample path
# ---------------------------------------------------------------------------

_ARXIV_SAMPLE = Path(__file__).parent / "fixtures" / "papers" / "arxiv_sample"
_FIXTURE_ARXIV_ID = "test-fixture"


# ---------------------------------------------------------------------------
# Fake Embedder (no model load, deterministic 384-dim vectors)
# ---------------------------------------------------------------------------


class FakeEmbedder:
    def embed(self, texts: list[str]) -> np.ndarray:
        rng = np.random.RandomState(42)
        vecs = rng.randn(len(texts), 384).astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.where(norms > 0, norms, 1.0)


# ---------------------------------------------------------------------------
# Fake ArxivResult (returned by mocked search_arxiv)
# ---------------------------------------------------------------------------

_FAKE_ARXIV_RESULT = ArxivResult(
    arxiv_id=_FIXTURE_ARXIV_ID,
    title="A Tiny Test Paper on Mixture of Experts",
    authors=["Test Author"],
    year=2024,
    abstract="Test abstract.",
    pdf_url=None,
)


# ---------------------------------------------------------------------------
# Pure-function tests (sync, no DB)
# ---------------------------------------------------------------------------


def test_compute_content_key_arxiv() -> None:
    key = compute_content_key(arxiv_id="2403.01234")
    assert key == "arxiv:2403.01234"


def test_compute_content_key_upload(tmp_path: Path) -> None:
    f = tmp_path / "test.pdf"
    f.write_bytes(b"hello world")
    expected_hex = hashlib.sha256(b"hello world").hexdigest()
    key = compute_content_key(upload_path=f)
    assert key == f"sha256:{expected_hex}"


def test_compute_content_key_requires_one_input() -> None:
    with pytest.raises(ValueError, match="must provide"):
        compute_content_key()


# ---------------------------------------------------------------------------
# Async integration fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pipeline_env(
    migrated_db: aiosqlite.Connection,
    tmp_path: Path,
) -> AsyncIterator[tuple[PaperPipeline, aiosqlite.Connection, Path]]:
    """Yields (pipeline, conn, cache_root) with a real migrated DB and tmp-path Chroma."""
    cache_root = tmp_path / "papers_cache"
    chroma_dir = tmp_path / "chroma"
    chroma = ChromaStore(chroma_dir)
    pipeline = PaperPipeline(
        migrated_db,
        papers_cache_dir=cache_root,
        chroma=chroma,
        embedder=FakeEmbedder(),
    )
    yield pipeline, migrated_db, cache_root


def _make_fake_download(source_dir: Path) -> MagicMock:
    """Return a MagicMock for download_arxiv_source that copies the fixture
    into the expected location under ``cache_root / arxiv / arxiv_id / source/``
    and returns that path.
    """

    def _fake_download(arxiv_id: str, *, cache_root: Path) -> Path:
        target = cache_root / arxiv_id / "source"
        target.mkdir(parents=True, exist_ok=True)
        for src in source_dir.iterdir():
            shutil.copy(src, target / src.name)
        return target

    mock = MagicMock(side_effect=_fake_download)
    return mock


def _fake_search_arxiv(query: str, max_results: int = 10) -> list[ArxivResult]:
    return [_FAKE_ARXIV_RESULT]


# ---------------------------------------------------------------------------
# Async tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_arxiv_cache_miss_creates_paper_content_and_chunks(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
    migrated_db: aiosqlite.Connection,
) -> None:
    pipeline, conn, cache_root = pipeline_env

    # Create a chat_sessions row so the FK to papers.session_id is satisfied.
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('test session')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    fake_download = _make_fake_download(_ARXIV_SAMPLE)

    with (
        patch(
            "paperhub.pipelines.paper_pipeline.download_arxiv_source",
            side_effect=fake_download,
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.search_arxiv",
            side_effect=_fake_search_arxiv,
        ),
    ):
        result = await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id=_FIXTURE_ARXIV_ID)
        )

    assert result.cache_hit is False

    # Verify paper_content row.
    async with conn.execute(
        "SELECT content_key, kind, arxiv_id, sha256, html_path FROM paper_content WHERE id = ?",
        (result.paper_content_id,),
    ) as cur:
        pc_row = await cur.fetchone()
    assert pc_row is not None
    content_key, kind, arxiv_id, sha256, html_path = pc_row
    assert content_key == f"arxiv:{_FIXTURE_ARXIV_ID}"
    assert kind == "arxiv"
    assert arxiv_id == _FIXTURE_ARXIV_ID
    assert sha256 is None
    assert html_path is not None
    assert os.path.exists(html_path)  # noqa: ASYNC240

    # Verify at least one chunks row.
    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?",
        (result.paper_content_id,),
    ) as cur:
        chunks_row = await cur.fetchone()
    assert chunks_row is not None
    assert int(chunks_row[0]) >= 1

    # Verify papers row linking session → paper_content.
    async with conn.execute(
        "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
        (session_id, result.paper_content_id),
    ) as cur:
        papers_row = await cur.fetchone()
    assert papers_row is not None
    assert int(papers_row[0]) == result.papers_id


@pytest.mark.asyncio
async def test_ingest_arxiv_cache_hit_skips_pipeline(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
    migrated_db: aiosqlite.Connection,
) -> None:
    pipeline, conn, cache_root = pipeline_env

    # Create a chat_sessions row.
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('test session')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    fake_download = _make_fake_download(_ARXIV_SAMPLE)
    download_mock = MagicMock(side_effect=fake_download)

    with (
        patch(
            "paperhub.pipelines.paper_pipeline.download_arxiv_source",
            new=download_mock,
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.search_arxiv",
            side_effect=_fake_search_arxiv,
        ),
    ):
        # First call — cache miss.
        result1 = await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id=_FIXTURE_ARXIV_ID)
        )
        # Second call — cache hit.
        result2 = await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id=_FIXTURE_ARXIV_ID)
        )

    assert result1.cache_hit is False
    assert result2.cache_hit is True
    assert result2.paper_content_id == result1.paper_content_id

    # download_arxiv_source must have been called exactly once (not twice).
    download_mock.assert_called_once()
