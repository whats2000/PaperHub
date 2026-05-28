"""Tests for paper_pipeline.py — cache-aware orchestrator (SRS §III-5.1)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio

from paperhub.pipelines.arxiv_client import ArxivResult
from paperhub.pipelines.marker_client import MarkerBlock, MarkerDoc
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
# Fake Marker client (no Docker/service; deterministic MarkerDoc)
# ---------------------------------------------------------------------------

# A 1x1 transparent PNG (valid, decodes cleanly with base64 validate=True).
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class _FakeMarker:
    """Stub MarkerClient.extract → a fixed MarkerDoc with one of each block."""

    def extract(self, pdf_bytes: bytes, *, max_pages: int | None = None) -> MarkerDoc:
        return MarkerDoc(
            blocks=[
                MarkerBlock(
                    block_type="SectionHeader",
                    html="<h2>Intro</h2>",
                    section_hierarchy={"1": "Intro"},
                    page=1,
                ),
                MarkerBlock(
                    block_type="Text",
                    html=(
                        "<p>Body text about transformers and attention "
                        "mechanisms used in modern models.</p>"
                    ),
                    section_hierarchy={"1": "Intro"},
                    page=1,
                ),
                MarkerBlock(
                    block_type="Figure",
                    html="<p>Figure 1: arch.</p>",
                    images={"fig.png": _TINY_PNG_B64},
                    section_hierarchy={"1": "Intro"},
                    page=1,
                ),
                MarkerBlock(
                    block_type="Equation",
                    latex="a^2+b^2=c^2",
                    section_hierarchy={"1": "Intro"},
                    page=1,
                ),
            ]
        )


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


@pytest.fixture(autouse=True)
def _marker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to a Marker-unreachable world (F2.1).

    PDF ingest now calls ``marker_available()`` synchronously to decide the
    persisted ``asset_status`` ('marker_pending' vs 'pymupdf_only'). Patch it
    to ``False`` by default so tests never hit a real socket; the one test that
    exercises the pending path overrides this locally.
    """
    monkeypatch.setattr(
        "paperhub.pipelines.paper_pipeline.marker_available", lambda: False
    )


@pytest_asyncio.fixture
async def pipeline_env_with_marker(
    migrated_db: aiosqlite.Connection,
    tmp_path: Path,
) -> AsyncIterator[tuple[PaperPipeline, aiosqlite.Connection, Path]]:
    """Like ``pipeline_env`` but injects a ``_FakeMarker``.

    F2.1: PDF ingest no longer calls Marker synchronously — it extracts with
    PyMuPDF and merely records whether a Marker upgrade should follow. The
    injected client is harmless (unused by ingest) and kept so a future
    background-worker test can reuse this fixture.
    """
    cache_root = tmp_path / "papers_cache"
    chroma_dir = tmp_path / "chroma"
    chroma = ChromaStore(chroma_dir)
    pipeline = PaperPipeline(
        migrated_db,
        papers_cache_dir=cache_root,
        chroma=chroma,
        embedder=FakeEmbedder(),
        marker_client=_FakeMarker(),
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
    assert result.title == _FAKE_ARXIV_RESULT.title

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
async def test_ingest_arxiv_renders_from_flattened_source(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
) -> None:
    """Regression (arxiv:2410.12557): HTML must be rendered from the FLATTENED
    single-file source, not the original main .tex. Rendering the un-flattened
    main file made pandoc expand \\input chains and hang/OOM, parking ingest.
    The flattened path is also what chunk offsets + sections_json align to."""
    pipeline, conn, _ = pipeline_env
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('s')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    captured: dict[str, Path] = {}

    def _capture_render(
        *, source: Path, kind: str, out_path: Path, resource_dir: Path | None = None,
        macros: dict[str, object] | None = None,
    ) -> Path:
        captured["source"] = source
        if resource_dir is not None:
            captured["resource_dir"] = resource_dir
        out_path.write_text("<html></html>", encoding="utf-8")
        return out_path

    with (
        patch(
            "paperhub.pipelines.paper_pipeline.download_arxiv_source",
            side_effect=_make_fake_download(_ARXIV_SAMPLE),
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.search_arxiv",
            side_effect=_fake_search_arxiv,
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.render_html",
            side_effect=_capture_render,
        ),
    ):
        await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id=_FIXTURE_ARXIV_ID)
        )

    # Renders from the figure-normalized flattened copy, NOT the original main
    # .tex (whose \input chains made pandoc hang/OOM).
    assert captured["source"].name == "source.render.tex"
    # Figures live in the extracted source tree, passed so pandoc can embed them.
    assert "resource_dir" in captured


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
    assert result1.title == _FAKE_ARXIV_RESULT.title
    assert result2.title == _FAKE_ARXIV_RESULT.title

    # download_arxiv_source must have been called exactly once (not twice).
    download_mock.assert_called_once()


# ---------------------------------------------------------------------------
# v2.4-5: ingest_pdf_from_url — PDF fallback for ss:<paperId> with no arxiv
# ---------------------------------------------------------------------------


_SAMPLE_PDF = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"


@pytest.mark.asyncio
async def test_ingest_pdf_from_url_persists_pdf_upload_kind(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
    migrated_db: aiosqlite.Connection,
) -> None:
    """Downloading a PDF via the open-access URL persists kind='pdf_upload'
    + sha256 content_key + chunks + papers row."""
    import httpx  # local to keep top-level imports minimal

    pipeline, conn, _cache = pipeline_env

    # Create a session so the FK is satisfied.
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('pdf test')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    pdf_bytes = _SAMPLE_PDF.read_bytes()

    # Patch httpx.AsyncClient.get to return our fixture PDF without hitting
    # the network. Use a transport-based mock.
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf_bytes)

    transport = httpx.MockTransport(_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with patch("paperhub.pipelines.paper_pipeline.httpx.AsyncClient",
               new=_PatchedClient):
        result = await pipeline.ingest_pdf_from_url(
            session_id=session_id,
            pdf_url="https://example.org/sample.pdf",
            title_hint="Sample PDF",
            abstract_hint="abs",
            authors_hint=["A"],
            year_hint=2024,
        )

    assert result.cache_hit is False
    assert result.title == "Sample PDF"

    async with conn.execute(
        "SELECT kind, content_key, sha256 FROM paper_content WHERE id = ?",
        (result.paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    kind, content_key, sha256 = row
    assert kind == "pdf_upload"
    assert content_key.startswith("sha256:")
    assert sha256 is not None
    assert content_key == f"sha256:{sha256}"

    # At least one chunk persisted.
    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?",
        (result.paper_content_id,),
    ) as cur:
        chunks_row = await cur.fetchone()
    assert chunks_row is not None
    assert int(chunks_row[0]) >= 1

    # PDF section navigation: sample.pdf has "Abstract" + "Introduction" at
    # 14pt (body 11pt, title 18pt), so heading detection must populate a
    # non-empty sections_json the paper_qa subagent can navigate.
    async with conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?",
        (result.paper_content_id,),
    ) as cur:
        sj_row = await cur.fetchone()
    assert sj_row is not None and sj_row[0] is not None
    section_names = {e["name"] for e in json.loads(sj_row[0])}
    assert "Abstract" in section_names
    assert "Introduction" in section_names
    # Those section names are queryable on chunks (what read_section uses).
    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ? AND section = ?",
        (result.paper_content_id, "Introduction"),
    ) as cur:
        sec_chunks = await cur.fetchone()
    assert sec_chunks is not None and int(sec_chunks[0]) >= 1

    # papers row links session to paper_content.
    async with conn.execute(
        "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
        (session_id, result.paper_content_id),
    ) as cur:
        papers_row = await cur.fetchone()
    assert papers_row is not None


# ---------------------------------------------------------------------------
# M1: behavior-level test — metadata_override skips _lookup_arxiv_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_arxiv_skips_lookup_when_metadata_override_provided(
    migrated_db: aiosqlite.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: when an IngestRequest carries a metadata_override,
    _ingest_arxiv must skip the arxiv metadata query entirely (Bug 1 fix
    for SS-driven adds — see commit d3834a6)."""
    from paperhub.pipelines.paper_pipeline import ArxivMetadata

    chroma = ChromaStore(tmp_path / "chroma")
    pipeline = PaperPipeline(
        migrated_db,
        papers_cache_dir=tmp_path / "papers_cache",
        chroma=chroma,
        embedder=FakeEmbedder(),
    )

    # Stub download_arxiv_source so we don't hit the network.
    # Copy the fixture source dir so the pipeline can parse it.
    fake_source_dir = tmp_path / "fake_source"
    fake_source_dir.mkdir()
    for src in _ARXIV_SAMPLE.iterdir():
        shutil.copy(src, fake_source_dir / src.name)

    def _fake_download(arxiv_id: str, *, cache_root: Path) -> Path:
        target = cache_root / arxiv_id / "source"
        target.mkdir(parents=True, exist_ok=True)
        for src in fake_source_dir.iterdir():
            shutil.copy(src, target / src.name)
        return target

    monkeypatch.setattr(
        "paperhub.pipelines.paper_pipeline.download_arxiv_source",
        _fake_download,
    )

    # Guard: _lookup_arxiv_metadata must NOT be called.
    lookup_mock = MagicMock(
        side_effect=AssertionError(
            "_lookup_arxiv_metadata must not be called when metadata_override is set"
        )
    )
    monkeypatch.setattr(pipeline, "_lookup_arxiv_metadata", lookup_mock)

    # Insert chat_sessions row so the papers FK doesn't fail.
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    async with migrated_db.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    override = ArxivMetadata(
        title="Override Title from SS",
        abstract="An abstract that arxiv never tells us about.",
        authors=["Alice"],
        year=2024,
    )
    result = await pipeline.ingest(
        IngestRequest(
            session_id=session_id,
            arxiv_id="9999.99999",
            metadata_override=override,
        )
    )

    assert result.title == "Override Title from SS"
    lookup_mock.assert_not_called()

    # Persisted paper_content row reflects the override.
    async with migrated_db.execute(
        "SELECT title, authors_json, year FROM paper_content WHERE id = ?",
        (result.paper_content_id,),
    ) as cur:
        pc_row = await cur.fetchone()
    assert pc_row is not None
    assert pc_row[0] == "Override Title from SS"


# ---------------------------------------------------------------------------
# v2.10-2: sections_json persisted at ingest time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_pipeline_persists_sections_json_at_ingest(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
    migrated_db: aiosqlite.Connection,
    tmp_path: Path,
) -> None:
    """After ingest, paper_content.sections_json must contain a list of
    {name, char_start, char_end, token_count, chunk_count} entries, ordered
    by appearance, covering every \\section{...} in the source."""
    import json

    pipeline, conn, cache_root = pipeline_env

    sample_tex = (
        "\\section{Introduction}\nIntro body here. " * 30 + "\n\n"
        "\\section{Method}\nMethod body here. " * 30 + "\n\n"
        "\\section{Experiments}\nExperiment body here. " * 50 + "\n"
    )

    # Build a fake latex source dir in tmp_path (like _make_fake_download does).
    src_dir = tmp_path / "sections_src"
    src_dir.mkdir()
    (src_dir / "main.tex").write_text(sample_tex, encoding="utf-8")

    # Create a chat_sessions row so the FK to papers.session_id is satisfied.
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('sections test')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    fake_download = _make_fake_download(src_dir)

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
            IngestRequest(session_id=session_id, arxiv_id="sections-test-fixture")
        )

    assert result.cache_hit is False

    async with conn.execute(
        "SELECT sections_json FROM paper_content WHERE id = ?",
        (result.paper_content_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None, "sections_json must be populated at ingest"
    sections = json.loads(row[0])
    assert [s["name"] for s in sections] == ["Introduction", "Method", "Experiments"]
    for s in sections:
        assert s["chunk_count"] > 0
        assert s["token_count"] > 0
        assert s["char_end"] > s["char_start"]
        for required_key in ("name", "char_start", "char_end", "token_count", "chunk_count"):
            assert required_key in s


# ---------------------------------------------------------------------------
# v2.10-2 review: _build_sections_json correctness — comment stripping
# ---------------------------------------------------------------------------


def test_build_sections_json_token_count_excludes_latex_comments() -> None:
    """Section token_count must reflect the post-comment-strip text so the
    subagent's section TOC doesn't overcount tokens. Regression: chunker
    strips locally, _build_sections_json was slicing the un-stripped
    caller copy.

    Strategy: build sections_json from a comment-heavy LaTeX string, then
    independently tokenize only the stripped content.  The reported
    token_count must match the stripped-content token count (within ±2
    tokenizer-boundary noise), not the un-stripped character count.
    """
    import json

    import tiktoken

    from paperhub.pipelines.chunker import chunk_text, strip_latex_comments

    # A single section with heavy inline comments — each line ends with
    # a LaTeX % comment that adds ~8 tokens if not stripped.
    source = "\\section{Method}\n" + (
        "Real content here. % this is a comment that must NOT count\n" * 80
    )

    chunks = chunk_text(source)
    sections_json = PaperPipeline._build_sections_json(chunks, source)
    reported_tokens = json.loads(sections_json)[0]["token_count"]

    # Ground-truth: tokenize only the stripped text between the chunk extents.
    stripped = strip_latex_comments(source)
    enc = tiktoken.get_encoding("cl100k_base")
    # Gather the same char extents _build_sections_json would use.
    method_chunks = [c for c in chunks if c.section == "Method"]
    assert method_chunks, "chunker should have produced at least one Method chunk"
    expected_text = stripped[method_chunks[0].char_start : method_chunks[-1].char_end]
    expected_tokens = len(enc.encode(expected_text))

    assert abs(reported_tokens - expected_tokens) <= 2, (
        f"reported token_count ({reported_tokens}) differs from stripped-content "
        f"token count ({expected_tokens}) by {abs(reported_tokens - expected_tokens)}; "
        "_build_sections_json is slicing the un-stripped (commented) text"
    )


# ---------------------------------------------------------------------------
# W6-2: sentinel injection in LaTeX render path → dom_id persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latex_ingest_persists_dom_ids(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
) -> None:
    """After LaTeX ingest, chunks must carry non-null dom_id values ('phchunk-N')
    and the final source.html must contain <span id="phchunk-0"> but NOT the raw
    PHCHUNKANCHOR sentinel token.

    render_html is stubbed to write the marked (sentinel-injected) source text
    directly to the HTML file — this simulates pandoc passing the sentinel tokens
    through unchanged (the real case for plain-text tokens in body paragraphs).
    """
    pipeline, conn, cache_root = pipeline_env

    # Build a tiny two-section LaTeX doc that produces at least 2 chunks.
    # Use 40 repetitions so each section is large enough to produce ≥1 chunk.
    sample_tex = (
        "\\section{Introduction}\n" + ("Intro body here is a sentence. " * 40) + "\n\n"
        "\\section{Method}\n" + ("Method body here is a sentence. " * 40) + "\n"
    )
    src_dir = cache_root / "sentinel_test_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "main.tex").write_text(sample_tex, encoding="utf-8")

    # Create a session.
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('sentinel test')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    captured_render_source: dict[str, str] = {}

    def _stub_render_html(
        *, source: Path, kind: str, out_path: Path, resource_dir: Path | None = None,
        macros: dict[str, object] | None = None,
    ) -> Path:
        """Write the marked source text verbatim as the HTML — sentinels survive."""
        content = source.read_text(encoding="utf-8")
        captured_render_source["content"] = content
        out_path.write_text(content, encoding="utf-8")
        return out_path

    with (
        patch(
            "paperhub.pipelines.paper_pipeline.download_arxiv_source",
            side_effect=_make_fake_download(src_dir),
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.search_arxiv",
            side_effect=_fake_search_arxiv,
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.render_html",
            side_effect=_stub_render_html,
        ),
    ):
        result = await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id="sentinel-test-w6-2")
        )

    assert result.cache_hit is False

    # (a) At least one chunk must have a non-null dom_id like 'phchunk-0'.
    async with conn.execute(
        "SELECT dom_id FROM chunks WHERE paper_content_id = ? ORDER BY id",
        (result.paper_content_id,),
    ) as cur:
        chunk_rows = await cur.fetchall()
    assert chunk_rows, "no chunks persisted"
    dom_ids = [row[0] for row in chunk_rows]
    non_null = [d for d in dom_ids if d is not None]
    assert non_null, (
        f"all dom_ids are null; expected at least phchunk-0. dom_ids={dom_ids}"
    )
    # The first non-null dom_id should follow the phchunk-N pattern.
    assert all(d.startswith("phchunk-") for d in non_null), (
        f"unexpected dom_id format: {non_null}"
    )

    # (b) The source.html must contain the <span id> anchor and NOT the raw token.
    async with conn.execute(
        "SELECT html_path FROM paper_content WHERE id = ?",
        (result.paper_content_id,),
    ) as cur:
        pc_row = await cur.fetchone()
    assert pc_row is not None
    html_content = Path(str(pc_row[0])).read_text(encoding="utf-8")  # noqa: ASYNC240
    assert '<span id="phchunk-0">' in html_content, (
        "source.html missing <span id=\"phchunk-0\"> anchor"
    )
    assert "PHCHUNKANCHOR" not in html_content, (
        "raw PHCHUNKANCHOR sentinel token found in source.html — postprocess_sentinels not applied"
    )

    # (c) Sentinel tokens must NOT appear in any chunk's text.
    async with conn.execute(
        "SELECT text FROM chunks WHERE paper_content_id = ?",
        (result.paper_content_id,),
    ) as cur:
        text_rows = await cur.fetchall()
    for (text,) in text_rows:
        assert "PHCHUNKANCHOR" not in text, (
            f"sentinel token leaked into chunk text: {text[:80]!r}"
        )


@pytest.mark.asyncio
async def test_pdf_upload_ingest_leaves_dom_id_null(
    pipeline_env_with_marker: tuple[PaperPipeline, aiosqlite.Connection, Path],
) -> None:
    """PDF-path chunks must keep dom_id=NULL — no sentinel injection for PDF papers."""
    pipeline, conn, _cache = pipeline_env_with_marker

    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('pdf dom_id test')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    # Use the existing sample PDF fixture.
    result = await pipeline.ingest(
        IngestRequest(
            session_id=session_id,
            upload_path=_SAMPLE_PDF,
            upload_kind="pdf",
        )
    )

    # All chunks for a PDF paper must have dom_id=NULL.
    async with conn.execute(
        "SELECT COUNT(*), COUNT(dom_id) FROM chunks WHERE paper_content_id = ?",
        (result.paper_content_id,),
    ) as cur:
        counts_row = await cur.fetchone()
    assert counts_row is not None
    total, non_null_count = int(counts_row[0]), int(counts_row[1])
    assert total >= 1, "PDF ingest produced no chunks"
    assert non_null_count == 0, (
        f"PDF chunks have non-null dom_id (expected 0, got {non_null_count}/{total})"
    )


# ---------------------------------------------------------------------------
# F2-T5: PDF ingest via Marker → PaperAsset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_ingest_uses_pymupdf_baseline_when_marker_unavailable(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
) -> None:
    """F2.1: PDF upload ingest extracts SYNCHRONOUSLY with PyMuPDF (no Marker
    call), writes a degraded PaperAsset baseline, produces RAG chunks, and —
    with Marker unreachable (autouse ``_marker_unavailable``) — persists
    ``asset_status='pymupdf_only'``."""
    from paperhub.pipelines.paper_asset import read_paper_asset

    pipeline, conn, _cache_root = pipeline_env

    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('pymupdf test')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    res = await pipeline.ingest(
        IngestRequest(
            session_id=session_id, upload_path=_SAMPLE_PDF, upload_kind="pdf",
        )
    )

    async with conn.execute(
        "SELECT source_dir_path, asset_status FROM paper_content WHERE id = ?",
        (res.paper_content_id,),
    ) as cur:
        sdp_row = await cur.fetchone()
    assert sdp_row is not None
    asset = read_paper_asset(Path(str(sdp_row[0])))
    assert asset is not None, "PyMuPDF baseline PaperAsset must be written"
    assert sdp_row[1] == "pymupdf_only"

    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?",
        (res.paper_content_id,),
    ) as cur:
        chunks_row = await cur.fetchone()
    assert chunks_row is not None
    assert int(chunks_row[0]) >= 1


@pytest.mark.asyncio
async def test_pdf_ingest_enqueues_marker_when_available(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Marker service IS reachable, PDF ingest still returns on the
    PyMuPDF baseline but records ``asset_status='marker_pending'`` so a
    background worker can run the high-fidelity upgrade later."""
    pipeline, conn, _cache_root = pipeline_env

    monkeypatch.setattr(
        "paperhub.pipelines.paper_pipeline.marker_available", lambda: True
    )

    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('pending test')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    res = await pipeline.ingest(
        IngestRequest(
            session_id=session_id, upload_path=_SAMPLE_PDF, upload_kind="pdf",
        )
    )

    async with conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?",
        (res.paper_content_id,),
    ) as cur:
        as_row = await cur.fetchone()
    assert as_row is not None
    assert as_row[0] == "marker_pending"


@pytest.mark.asyncio
async def test_arxiv_latex_ingest_asset_status_is_latex(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
) -> None:
    """arXiv LaTeX ingest builds the asset synchronously from source, so its
    ``asset_status`` is 'latex' (never a Marker-pending PDF state)."""
    pipeline, conn, _ = pipeline_env

    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('latex status')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    with (
        patch(
            "paperhub.pipelines.paper_pipeline.download_arxiv_source",
            side_effect=_make_fake_download(_ARXIV_SAMPLE),
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.search_arxiv",
            side_effect=_fake_search_arxiv,
        ),
    ):
        res = await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id=_FIXTURE_ARXIV_ID)
        )

    async with conn.execute(
        "SELECT asset_status FROM paper_content WHERE id = ?",
        (res.paper_content_id,),
    ) as cur:
        as_row = await cur.fetchone()
    assert as_row is not None
    assert as_row[0] == "latex"


# ---------------------------------------------------------------------------
# F2-T6: arXiv ingest emits PaperAsset (additive)
# ---------------------------------------------------------------------------

# A 1x1 PNG in raw bytes (same as _TINY_PNG_B64 decoded above).
_TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\x9cc\xf8"
    b"\xcf@\x0f\x00\x03\x86\x01\x80Z4}k\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_arxiv_ingest_emits_paper_asset(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
    tmp_path: Path,
) -> None:
    """After arXiv ingest (LaTeX path), read_paper_asset must return a PaperAsset
    with the sections, figures, and equations from the fixture source."""
    from paperhub.pipelines.paper_asset import read_paper_asset

    pipeline, conn, cache_root = pipeline_env

    # Build a tiny fake source dir: section + figure + equation.
    fake_src = tmp_path / "fixture_src"
    (fake_src / "figs").mkdir(parents=True)
    (fake_src / "figs" / "f.png").write_bytes(_TINY_PNG_BYTES)
    (fake_src / "main.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\section{Intro}
Some body text here is present in the introduction section of this document.
\begin{figure}
\includegraphics{figs/f}
\caption{A diagram.}
\end{figure}
\begin{equation}E=mc^2\end{equation}
\end{document}""",
        encoding="utf-8",
    )

    def fake_dl(arxiv_id: str, *, cache_root: Path) -> Path:
        target = cache_root / arxiv_id / "source"
        target.mkdir(parents=True, exist_ok=True)
        for p in fake_src.rglob("*"):
            if p.is_file():
                d = target / p.relative_to(fake_src)
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, d)
        return target

    # Create a session so the papers FK is satisfied.
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('asset test')")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    session_id = int(row[0])

    with (
        patch(
            "paperhub.pipelines.paper_pipeline.download_arxiv_source",
            side_effect=fake_dl,
        ),
        patch(
            "paperhub.pipelines.paper_pipeline.search_arxiv",
            side_effect=_fake_search_arxiv,
        ),
    ):
        res = await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id="1234.5678")
        )

    # Read the source_dir_path back from the DB and check the asset.
    async with conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?",
        (res.paper_content_id,),
    ) as cur:
        sdp_row = await cur.fetchone()
    assert sdp_row is not None
    asset = read_paper_asset(Path(str(sdp_row[0])))

    assert asset is not None, "PaperAsset must be written for arXiv LaTeX ingest"
    assert any(s.name == "Intro" for s in asset.sections), (
        f"Expected section 'Intro' in asset.sections; got {[s.name for s in asset.sections]}"
    )
    assert any("diagram" in f.caption.lower() for f in asset.figures), (
        f"Expected a figure with 'diagram' in caption; got {[f.caption for f in asset.figures]}"
    )
    assert any("mc^2" in e.latex for e in asset.equations), (
        f"Expected equation with 'mc^2'; got {[e.latex for e in asset.equations]}"
    )


# ---------------------------------------------------------------------------
# Concurrent write-lock regression (v2.23.2 hotfix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_writes_serialise_through_write_transaction(
    pipeline_env: tuple[PaperPipeline, aiosqlite.Connection, Path],
    tmp_path: Path,
) -> None:
    """The two contending writes in the ingest path —
    ``_persist_paper_content_and_chunks`` and ``_link_to_session`` — must
    survive concurrent invocation without raising
    ``sqlite3.OperationalError: database is locked``.

    Reproduces the v2.23.1 deployment bug where rapid back-to-back
    ``POST /papers`` requests crashed two of three ingests because the
    persist transaction (~400 INSERTs per paper) and the post-persist link
    INSERT raced on the SQLite write lock. The fix routes both through
    ``write_transaction()`` in ``paperhub.db.connection``, which holds a
    process-wide asyncio lock + uses ``BEGIN IMMEDIATE`` — so the database
    file never sees more than one writer at a time from this process.

    The test fires three full _persist+_link sequences in parallel and
    asserts (1) all three return distinct paper_content ids, and (2) all
    three are linked to the same session via distinct ``papers`` rows.
    """
    import asyncio

    from paperhub.pipelines.chunker import Chunk

    pipeline, conn, _cache_root = pipeline_env

    # Seed a session so the FK to papers.session_id is satisfied.
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()

    def _make_chunks(prefix: str, count: int = 200) -> list[Chunk]:
        # Mirror prod payload size — the bug only surfaces when the write
        # transaction is long (hundreds of INSERTs), not on a 1-chunk paper.
        return [
            Chunk(
                section="Intro",
                char_start=i * 10,
                char_end=i * 10 + 9,
                text=f"{prefix} chunk {i}",
                dom_id=None,
                match_text=None,
                page=None,
                bbox=None,
            )
            for i in range(count)
        ]

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_path = src_dir / "paper.tex"
    src_path.write_text("dummy")
    html_path = src_dir / "paper.html"
    html_path.write_text("<html></html>")

    async def _full_attach(n: int) -> tuple[int, int]:
        # Mirror _ingest_arxiv's ordering: persist first (long transaction),
        # then link to session (short transaction). Both must be serialised.
        pcid, _cids = await pipeline._persist_paper_content_and_chunks(
            content_key=f"test:concurrent-{n}",
            kind="arxiv",
            arxiv_id=f"test-{n}",
            sha256=None,
            metadata={
                "title": f"Concurrent paper {n}",
                "authors": ["A"],
                "year": 2026,
                "abstract": "x",
            },
            source_path=src_path,
            source_dir_path=src_dir,
            html_path=html_path,
            chunks=_make_chunks(f"p{n}"),
            sections_json=None,
            asset_status="latex",
        )
        papers_id = await pipeline._link_to_session(
            session_id=1, paper_content_id=pcid,
        )
        return pcid, papers_id

    results = await asyncio.gather(_full_attach(1), _full_attach(2), _full_attach(3))
    pcids = [r[0] for r in results]
    papers_ids = [r[1] for r in results]
    assert len(set(pcids)) == 3, (
        f"expected 3 distinct paper_content ids, got {pcids}"
    )
    assert len(set(papers_ids)) == 3, (
        f"expected 3 distinct papers row ids, got {papers_ids}"
    )
