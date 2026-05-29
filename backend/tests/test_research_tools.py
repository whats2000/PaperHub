"""Tests for research_tools dispatchers (SRS v2.4, FR-07)."""
from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import httpx
import pytest
import respx

from paperhub.agents.research_tools import (
    _BASE_PAPER_TOOL_SCHEMAS,
    NoIngestibleSourceError,
    _to_fts5_query,
    add_paper_to_session_dispatch,
    build_tool_schemas,
    search_library_dispatch,
)
from paperhub.pipelines.paper_pipeline import (
    ArxivMetadata,
    IngestRequest,
    IngestResult,
    PaperPipeline,
)
from paperhub.pipelines.semantic_scholar import SemanticScholarMetadata
from paperhub.pipelines.unpaywall import UNPAYWALL_BASE

# Note: pyproject sets ``asyncio_mode = "auto"`` — async test functions are
# auto-marked, so no module-level ``pytestmark`` is needed. Applying one would
# emit ``PytestWarning: marked with '@pytest.mark.asyncio' but it is not an
# async function`` for every sync test in this file.


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
        f"library:{pcid}",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
    )
    r2 = await add_paper_to_session_dispatch(
        f"library:{pcid}",
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
        "arxiv:2403.12345",
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
            "garbage:1",
            pipeline=pipeline,
            conn=migrated_db,
            session_id=1,
        )


# ---------------------------------------------------------------------------
# v2.4-5: LLM palette guardrails — search_arxiv + add_paper_to_session are
# removed; search_semantic_scholar is added.
# ---------------------------------------------------------------------------


def _schema_names() -> set[str]:
    return {s["function"]["name"] for s in _BASE_PAPER_TOOL_SCHEMAS}


def test_search_arxiv_not_in_tool_schemas() -> None:
    assert "search_arxiv" not in _schema_names()


def test_add_paper_to_session_not_in_tool_schemas() -> None:
    """v2.4 invariant: agent has no write tool."""
    assert "add_paper_to_session" not in _schema_names()


def test_search_semantic_scholar_in_tool_schemas() -> None:
    names = _schema_names()
    assert "search_semantic_scholar" in names


def test_tool_schemas_v2_4_palette_exact() -> None:
    """The base FastMCP ``papers.*`` palette contains exactly three entries."""
    assert _schema_names() == {
        "search_library",
        "search_semantic_scholar",
        "find_related_papers",
    }


async def test_build_tool_schemas_delegates_to_registry() -> None:
    """``build_tool_schemas`` returns the registry's aggregated schemas
    verbatim — no in-process fallback (Task v2.5-4 invariant)."""
    canned = [
        {
            "type": "function",
            "function": {
                "name": "papers.search_library",
                "description": "stub", "parameters": {},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web.search",
                "description": "stub", "parameters": {},
            },
        },
    ]

    class _FakeRegistry:
        async def aggregate_tool_schemas(self) -> list[Any]:
            return list(canned)

        async def has_tool(self, name: str) -> bool:
            return any(s.get("function", {}).get("name") == name for s in canned)

    out = await build_tool_schemas(_FakeRegistry())  # type: ignore[arg-type]
    assert out == canned


def test_add_paper_to_session_dispatch_signature_no_reason_param() -> None:
    """Drop of ``reason`` is part of the suggest-only design — the only
    callers (POST /papers + chat-endpoint auto-attach) have no reason."""
    sig = inspect.signature(add_paper_to_session_dispatch)
    assert "reason" not in sig.parameters, (
        f"Expected no 'reason' parameter, got: {list(sig.parameters)}"
    )


async def test_add_paper_to_session_dispatch_ss_with_arxiv_prefers_arxiv_path(
    migrated_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ss:<paperId> + SS metadata has externalIds.ArXiv → arxiv path.

    Additionally asserts that _lookup_arxiv_metadata is NOT called (SS
    metadata is passed via metadata_override — fix for the 429 bug).
    """
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)
    pipeline.ingest = AsyncMock(
        return_value=IngestResult(
            paper_content_id=42, papers_id=7, cache_hit=False, title="A Paper",
        ),
    )
    pipeline.ingest_pdf_from_url = AsyncMock()
    # Attach a spy so we can assert _lookup_arxiv_metadata is NOT invoked.
    pipeline._lookup_arxiv_metadata = MagicMock(
        side_effect=AssertionError("_lookup_arxiv_metadata must not be called when SS metadata is provided"),
    )

    async def _fake_meta(paper_id: str) -> SemanticScholarMetadata:
        return SemanticScholarMetadata(
            paperId=paper_id,
            title="A Paper",
            abstract="abs",
            year=2024,
            authors=["A"],
            arxiv_id="2401.99999",
            open_access_pdf_url="https://example.org/x.pdf",  # should NOT be used
            doi=None,
        )

    monkeypatch.setattr(
        "paperhub.agents.research_tools.fetch_paper_metadata", _fake_meta,
    )

    result = await add_paper_to_session_dispatch(
        "ss:abcd1234",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
    )
    pipeline.ingest.assert_awaited_once()
    pipeline.ingest_pdf_from_url.assert_not_called()
    # Verify that the IngestRequest carries the metadata_override so
    # PaperPipeline.ingest can skip _lookup_arxiv_metadata.
    call_args = pipeline.ingest.await_args
    assert call_args is not None
    sent: IngestRequest = call_args.args[0]
    assert sent.metadata_override is not None
    assert sent.metadata_override.title == "A Paper"
    assert sent.metadata_override.abstract == "abs"
    assert sent.metadata_override.year == 2024
    assert result.paper_content_id == 42


async def test_add_paper_to_session_dispatch_ss_falls_back_to_pdf(
    migrated_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ss:<paperId> + no arXiv + openAccessPdf.url → PDF download path."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)
    pipeline.ingest = AsyncMock()
    pipeline.ingest_pdf_from_url = AsyncMock(
        return_value=IngestResult(
            paper_content_id=43, papers_id=8, cache_hit=False, title="A PDF",
        ),
    )

    async def _fake_meta(paper_id: str) -> SemanticScholarMetadata:
        return SemanticScholarMetadata(
            paperId=paper_id,
            title="A PDF",
            abstract="abs",
            year=2024,
            authors=["B"],
            arxiv_id=None,
            open_access_pdf_url="https://example.org/x.pdf",
            doi=None,
        )

    monkeypatch.setattr(
        "paperhub.agents.research_tools.fetch_paper_metadata", _fake_meta,
    )

    result = await add_paper_to_session_dispatch(
        "ss:noarxiv",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
    )
    pipeline.ingest.assert_not_called()
    pipeline.ingest_pdf_from_url.assert_awaited_once()
    assert result.title == "A PDF"


async def test_add_paper_to_session_dispatch_ss_raises_NoIngestibleSourceError_when_no_source(
    migrated_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ss:<paperId> + no arXiv + no openAccessPdf.url → NoIngestibleSourceError."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)

    async def _fake_meta(paper_id: str) -> SemanticScholarMetadata:
        return SemanticScholarMetadata(
            paperId=paper_id,
            title="No Source",
            abstract="abs",
            year=2024,
            authors=[],
            arxiv_id=None,
            open_access_pdf_url=None,
            doi=None,
        )

    monkeypatch.setattr(
        "paperhub.agents.research_tools.fetch_paper_metadata", _fake_meta,
    )

    with pytest.raises(NoIngestibleSourceError) as exc_info:
        await add_paper_to_session_dispatch(
            "ss:nosrc",
            pipeline=pipeline,
            conn=migrated_db,
            session_id=session_id,
        )
    assert exc_info.value.paper_id == "ss:nosrc"
    assert exc_info.value.title == "No Source"


# ---------------------------------------------------------------------------
# FTS5 helpers
# ---------------------------------------------------------------------------


def test_to_fts5_query_single_token() -> None:
    assert _to_fts5_query("transformer") == '"transformer"'


def test_to_fts5_query_multi_word_ands() -> None:
    assert _to_fts5_query("transformers attention") == '"transformers" AND "attention"'


def test_to_fts5_query_strips_operators() -> None:
    # FTS5 special chars should be stripped, not passed through.
    # Tokens separated by spaces: special chars within each token are dropped.
    assert _to_fts5_query('"transformers" -attention') == '"transformers" AND "attention"'


@pytest.mark.parametrize(
    "query,expected_in_match",
    [
        ("attention AND transformer", ['"attention"', '"AND"', '"transformer"']),
        ("attention OR transformer", ['"attention"', '"OR"', '"transformer"']),
        ("attention NOT transformer", ['"attention"', '"NOT"', '"transformer"']),
        ("attention NEAR transformer", ['"attention"', '"NEAR"', '"transformer"']),
        ("Pros AND Cons", ['"Pros"', '"AND"', '"Cons"']),
    ],
)
def test_to_fts5_query_quotes_reserved_keywords_as_literals(
    query: str, expected_in_match: list[str]
) -> None:
    out = _to_fts5_query(query)
    for tok in expected_in_match:
        assert tok in out, f"expected {tok!r} as literal phrase in {out!r}"
    assert out.count(" AND ") == len(expected_in_match) - 1


def test_to_fts5_query_empty_returns_empty() -> None:
    assert _to_fts5_query("") == ""
    assert _to_fts5_query("   ") == ""


async def test_search_library_matches_multi_word_queries(
    migrated_db: aiosqlite.Connection,
) -> None:
    """FTS5 MATCH should hit 'On Transformers and Attention' for the
    two-word query 'transformers attention'."""
    session_id = await _make_session(migrated_db)
    pcid = await _insert_paper_content(
        migrated_db,
        arxiv_id="2401.11111",
        title="On Transformers and Attention",
        abstract="We study self-attention in transformer models.",
    )
    # Unrelated paper that must not appear.
    await _insert_paper_content(
        migrated_db,
        arxiv_id="2401.22222",
        title="Convolutional Neural Networks",
        abstract="CNN-based image classification.",
    )

    hits = await search_library_dispatch(
        query="transformers attention",
        conn=migrated_db,
        session_id=session_id,
    )
    ids = {h.paper_content_id for h in hits}
    assert pcid in ids, "Multi-word FTS5 query must match the transformer paper"


async def test_search_library_empty_query_returns_empty(
    migrated_db: aiosqlite.Connection,
) -> None:
    """A blank query should return an empty list, not error."""
    session_id = await _make_session(migrated_db)
    await _insert_paper_content(
        migrated_db,
        arxiv_id="2401.33333",
        title="Some Paper",
        abstract="Some abstract.",
    )
    hits = await search_library_dispatch(
        query="   ",
        conn=migrated_db,
        session_id=session_id,
    )
    assert hits == []


async def test_add_paper_to_session_dispatch_arxiv_threads_metadata_override(
    migrated_db: aiosqlite.Connection,
) -> None:
    """arxiv: branch forwards caller-supplied metadata_override to the
    pipeline so the arXiv metadata API is skipped (M2 fix)."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)
    pipeline.ingest = AsyncMock(
        return_value=IngestResult(
            paper_content_id=55, papers_id=66, cache_hit=False,
            title="Override Title",
        ),
    )

    override = ArxivMetadata(
        title="Override Title",
        abstract="Override abstract.",
        authors=["Alice", "Bob"],
        year=2023,
    )
    result = await add_paper_to_session_dispatch(
        "arxiv:2301.00001",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
        metadata_override=override,
    )

    pipeline.ingest.assert_awaited_once()
    call_args = pipeline.ingest.await_args
    assert call_args is not None
    sent: IngestRequest = call_args.args[0]
    assert isinstance(sent, IngestRequest)
    assert sent.metadata_override is override
    assert result.paper_content_id == 55
    assert result.title == "Override Title"


async def test_add_paper_to_session_dispatch_arxiv_no_override_passes_none(
    migrated_db: aiosqlite.Connection,
) -> None:
    """arxiv: branch without a caller-supplied override passes
    metadata_override=None to the pipeline (regression guard)."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)
    pipeline.ingest = AsyncMock(
        return_value=IngestResult(
            paper_content_id=77, papers_id=88, cache_hit=False, title="No Override",
        ),
    )

    await add_paper_to_session_dispatch(
        "arxiv:2301.99999",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
    )

    call_args = pipeline.ingest.await_args
    assert call_args is not None
    sent: IngestRequest = call_args.args[0]
    assert sent.metadata_override is None


async def test_search_library_handles_reserved_keyword_queries(
    migrated_db: aiosqlite.Connection,
) -> None:
    """User queries containing FTS5 reserved keywords (AND/OR/NOT/NEAR) must
    not crash the dispatcher — they should be treated as literal tokens."""
    session_id = await _make_session(migrated_db)
    # Seed a paper whose text contains the literal word "AND" and "attention".
    await _insert_paper_content(
        migrated_db,
        arxiv_id="2401.44444",
        title="Attention AND Transformers",
        abstract="We study attention and transformer interactions.",
    )
    # Call with a query that would previously cause an FTS5 syntax error.
    hits = await search_library_dispatch(
        query="attention AND transformer",
        max_results=5,
        conn=migrated_db,
        session_id=session_id,
    )
    assert isinstance(hits, list)


# ---------------------------------------------------------------------------
# F4.3: Unpaywall dispatch branch — ss: with no arxiv, no openAccessPdf, has DOI
# ---------------------------------------------------------------------------

_SS_DOI = "10.1038/test"
_UNPAYWALL_PDF_URL = "https://example.org/test.pdf"
_UNPAYWALL_ENDPOINT = f"{UNPAYWALL_BASE}/{_SS_DOI}"


def _ss_meta_doi_only(paper_id: str) -> SemanticScholarMetadata:
    """SS metadata with DOI but no arxiv_id and no openAccessPdf."""
    return SemanticScholarMetadata(
        paperId=paper_id,
        title="Nature Paper",
        abstract="A paper published only on Nature.",
        year=2025,
        authors=["Alice", "Bob"],
        arxiv_id=None,
        open_access_pdf_url=None,
        doi=_SS_DOI,
    )


@respx.mock
async def test_dispatch_ss_unpaywall_happy_path(
    migrated_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ss: + no arxiv, no openAccessPdf, has DOI + Unpaywall returns PDF URL
    → ingest_pdf_from_url is called with the Unpaywall URL."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)
    pipeline.ingest_pdf_from_url = AsyncMock(
        return_value=IngestResult(
            paper_content_id=101, papers_id=201, cache_hit=False, title="Nature Paper",
        ),
    )

    async def _fake_meta(paper_id: str) -> SemanticScholarMetadata:
        return _ss_meta_doi_only(paper_id)

    monkeypatch.setattr(
        "paperhub.agents.research_tools.fetch_paper_metadata", _fake_meta,
    )

    respx.get(_UNPAYWALL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "is_oa": True,
                "best_oa_location": {
                    "url_for_pdf": _UNPAYWALL_PDF_URL,
                    "url": "https://example.org/test",
                },
            },
        ),
    )

    result = await add_paper_to_session_dispatch(
        "ss:abc",
        pipeline=pipeline,
        conn=migrated_db,
        session_id=session_id,
        unpaywall_email="ops@example.com",
    )

    pipeline.ingest_pdf_from_url.assert_awaited_once()
    call_kwargs = pipeline.ingest_pdf_from_url.await_args
    assert call_kwargs is not None
    assert call_kwargs.kwargs["pdf_url"] == _UNPAYWALL_PDF_URL
    assert result.paper_content_id == 101
    assert result.title == "Nature Paper"


@respx.mock
async def test_dispatch_ss_unpaywall_no_oa_url_raises_not_ingestible(
    migrated_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ss: + no arxiv, no openAccessPdf, has DOI + Unpaywall returns is_oa=false
    → NoIngestibleSourceError raised (same as F4.2 behaviour)."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)

    async def _fake_meta(paper_id: str) -> SemanticScholarMetadata:
        return _ss_meta_doi_only(paper_id)

    monkeypatch.setattr(
        "paperhub.agents.research_tools.fetch_paper_metadata", _fake_meta,
    )

    respx.get(_UNPAYWALL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={"is_oa": False},
        ),
    )

    with pytest.raises(NoIngestibleSourceError) as exc_info:
        await add_paper_to_session_dispatch(
            "ss:abc",
            pipeline=pipeline,
            conn=migrated_db,
            session_id=session_id,
            unpaywall_email="ops@example.com",
        )
    assert exc_info.value.paper_id == "ss:abc"
    assert exc_info.value.title == "Nature Paper"


@respx.mock
async def test_dispatch_ss_no_unpaywall_email_skips_fallback(
    migrated_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ss: + no arxiv, no openAccessPdf, has DOI + unpaywall_email=None
    → Unpaywall endpoint NEVER called, NoIngestibleSourceError raised.
    This is the graceful-degradation guarantee: no env var, no fallback."""
    session_id = await _make_session(migrated_db)
    pipeline = MagicMock(spec=PaperPipeline)

    async def _fake_meta(paper_id: str) -> SemanticScholarMetadata:
        return _ss_meta_doi_only(paper_id)

    monkeypatch.setattr(
        "paperhub.agents.research_tools.fetch_paper_metadata", _fake_meta,
    )

    unpaywall_route = respx.get(_UNPAYWALL_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"is_oa": True}),
    )

    with pytest.raises(NoIngestibleSourceError) as exc_info:
        await add_paper_to_session_dispatch(
            "ss:abc",
            pipeline=pipeline,
            conn=migrated_db,
            session_id=session_id,
            unpaywall_email=None,
        )

    assert not unpaywall_route.called, "Unpaywall must NOT be called when unpaywall_email=None"
    assert exc_info.value.paper_id == "ss:abc"
    assert exc_info.value.title == "Nature Paper"
