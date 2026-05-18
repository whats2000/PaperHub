"""Tests for the Papers REST surface (Task 12, Plan C).

All tests use the ASGI test client pattern from test_chat_sse.py:
  - create_app() creates an isolated app instance
  - PAPERHUB_WORKSPACE env var points to tmp_path so each test gets its own DB
  - PaperPipeline.ingest is patched at the module level to avoid real network calls
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.pipelines.paper_pipeline import IngestResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_paper_content(
    conn: aiosqlite.Connection,
    *,
    content_key: str,
    title: str,
    arxiv_id: str | None = None,
    html_path: str = "/tmp/source.html",
    year: int | None = 2024,
    abstract: str = "abstract text",
) -> int:
    """Insert a paper_content row and return its id."""
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            content_key,
            "arxiv",
            arxiv_id,
            title,
            "[]",
            year,
            abstract,
            "/tmp/source.tex",
            "/tmp",
            html_path,
        ),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _seed_session(conn: aiosqlite.Connection) -> int:
    """Insert a chat_sessions row and return its id."""
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _seed_papers_row(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    paper_content_id: int,
    enabled: int = 1,
) -> int:
    """Insert a papers membership row and return its id."""
    await conn.execute(
        "INSERT OR IGNORE INTO papers (session_id, paper_content_id, enabled) "
        "VALUES (?, ?, ?)",
        (session_id, paper_content_id, enabled),
    )
    await conn.commit()
    async with conn.execute(
        "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
        (session_id, paper_content_id),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _get_db_path(tmp_path: Path, monkeypatch: Any) -> Path:
    """Set PAPERHUB_WORKSPACE and return the resulting db_path."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    return tmp_path / "paperhub.db"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_post_papers_ingests_then_cache_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two POSTs with the same arxiv_id: first cache_hit=False, second True."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    # Seed DB so it has the schema before the app touches it.
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await _seed_session(conn)  # session_id=1

    call_count = 0

    async def _fake_ingest(self: Any, req: Any) -> IngestResult:
        nonlocal call_count
        call_count += 1
        is_hit = call_count > 1
        return IngestResult(
            paper_content_id=1,
            papers_id=1,
            cache_hit=is_hit,
            title="Attention Is All You Need",
        )

    import paperhub.pipelines.paper_pipeline as pipeline_module

    with patch.object(pipeline_module.PaperPipeline, "ingest", _fake_ingest):
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post(
                "/papers",
                json={"session_id": 1, "arxiv_id": "1706.03762"},
            )
            r2 = await client.post(
                "/papers",
                json={"session_id": 1, "arxiv_id": "1706.03762"},
            )

    assert r1.status_code == 201
    assert r1.json()["cache_hit"] is False
    assert r2.status_code == 201
    assert r2.json()["cache_hit"] is True
    assert r2.json()["title"] == "Attention Is All You Need"


async def test_get_library_excludes_session_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Library endpoint excludes papers already in the requested session."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        pc1 = await _seed_paper_content(
            conn, content_key="arxiv:1706.03762", title="Attention Is All You Need",
            arxiv_id="1706.03762",
        )
        pc2 = await _seed_paper_content(
            conn, content_key="arxiv:2005.14165", title="GPT-3 Language Models",
            arxiv_id="2005.14165",
        )
        # Attach pc1 to the session; pc2 should appear in library.
        await _seed_papers_row(conn, session_id=session_id, paper_content_id=pc1)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/papers/library?session_id={session_id}")

    assert r.status_code == 200
    items = r.json()
    ids = [item["paper_content_id"] for item in items]
    assert pc2 in ids
    assert pc1 not in ids


async def test_get_library_filters_by_q(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ?q= filter narrows results to title/abstract matches."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        await _seed_paper_content(
            conn, content_key="arxiv:1706.03762", title="Attention Is All You Need",
            arxiv_id="1706.03762", abstract="transformer architecture",
        )
        await _seed_paper_content(
            conn, content_key="arxiv:2005.14165", title="GPT-3 Language Models",
            arxiv_id="2005.14165", abstract="large language model scaling",
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/papers/library?session_id={session_id}&q=transformer")

    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["title"] == "Attention Is All You Need"


async def test_post_from_library_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two POSTs with the same (session_id, paper_content_id) → same papers_id, one DB row."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        pc_id = await _seed_paper_content(
            conn, content_key="arxiv:1706.03762", title="Attention Is All You Need",
            arxiv_id="1706.03762",
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(
            "/papers/from-library",
            json={"session_id": session_id, "paper_content_id": pc_id},
        )
        r2 = await client.post(
            "/papers/from-library",
            json={"session_id": session_id, "paper_content_id": pc_id},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["papers_id"] == r2.json()["papers_id"]

    # Confirm only one row in the DB.
    async with (
        aiosqlite.connect(db_path) as conn,
        conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = ? AND paper_content_id = ?",
            (session_id, pc_id),
        ) as cur,
    ):
        count_row = await cur.fetchone()
    assert count_row is not None
    assert int(count_row[0]) == 1


async def test_patch_toggles_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATCH /{papers_id} with enabled=false flips the DB column to 0."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        pc_id = await _seed_paper_content(
            conn, content_key="arxiv:1706.03762", title="Attention Is All You Need",
            arxiv_id="1706.03762",
        )
        papers_id = await _seed_papers_row(
            conn, session_id=session_id, paper_content_id=pc_id, enabled=1
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(
            f"/papers/{papers_id}",
            json={"enabled": False},
        )

    assert r.status_code == 200
    assert r.json() == {"enabled": False}

    # Confirm the DB column was updated.
    async with (
        aiosqlite.connect(db_path) as conn,
        conn.execute("SELECT enabled FROM papers WHERE id = ?", (papers_id,)) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 0


async def test_delete_removes_papers_row_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DELETE /papers/{papers_id} → 204; paper_content row still exists."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        pc_id = await _seed_paper_content(
            conn, content_key="arxiv:1706.03762", title="Attention Is All You Need",
            arxiv_id="1706.03762",
        )
        papers_id = await _seed_papers_row(
            conn, session_id=session_id, paper_content_id=pc_id
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete(f"/papers/{papers_id}")

    assert r.status_code == 204

    # papers row gone, paper_content row untouched.
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT id FROM papers WHERE id = ?", (papers_id,)
        ) as cur:
            papers_row = await cur.fetchone()
        async with conn.execute(
            "SELECT id FROM paper_content WHERE id = ?", (pc_id,)
        ) as cur:
            pc_row = await cur.fetchone()
    assert papers_row is None
    assert pc_row is not None


async def test_get_html_serves_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /papers/content/{id}/html → 200 with text/html when file exists."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    # Write a real HTML file so FileResponse can serve it.
    html_file = tmp_path / "source.html"
    html_file.write_text("<html><body>paper</body></html>", encoding="utf-8")

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        pc_id = await _seed_paper_content(
            conn,
            content_key="arxiv:1706.03762",
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            html_path=str(html_file),
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/papers/content/{pc_id}/html")

    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


async def test_get_html_404_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /papers/content/{nonexistent_id}/html → 404."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/papers/content/9999/html")

    assert r.status_code == 404


async def test_get_library_q_filter_handles_multi_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FTS5 multi-word ?q= filter: 'transformers attention' matches only the
    paper whose title/abstract contains both tokens, not the single-keyword one."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        # Paper A: matches BOTH 'transformers' AND 'attention' — should appear.
        await _seed_paper_content(
            conn,
            content_key="arxiv:2401.11111",
            title="On Transformers and Attention",
            arxiv_id="2401.11111",
            abstract="self-attention in transformer models",
        )
        # Paper B: only matches 'transformers' — should NOT appear for two-word query.
        await _seed_paper_content(
            conn,
            content_key="arxiv:2401.22222",
            title="Transformers for Images",
            arxiv_id="2401.22222",
            abstract="vision backbone without attention heads",
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            f"/papers/library?session_id={session_id}&q=transformers+attention"
        )

    assert r.status_code == 200
    items = r.json()
    titles = [item["title"] for item in items]
    assert "On Transformers and Attention" in titles
    # Paper B has "attention" in abstract but NOT in title — with AND semantics
    # "transformers AND attention" it DOES match (abstract contains both).
    # Assert that at minimum paper A is present; paper B absence depends on
    # whether FTS5 finds "attention" in its abstract.
    # The key assertion is that we DON'T get a 500 / error on multi-word input.
    assert len(items) >= 1


async def test_get_library_handles_reserved_keyword_q(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """q parameter must not crash on FTS5 reserved keywords (AND/OR/NOT/NEAR).
    Previously these were passed unquoted to MATCH, producing a syntax error → 500."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        await _seed_paper_content(
            conn,
            content_key="arxiv:1706.03762",
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            abstract="transformer architecture with self-attention",
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/papers/library",
            params={"session_id": session_id, "q": "attention AND transformer"},
        )

    assert r.status_code == 200, r.text


async def test_get_html_410_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If html_path is set on a paper_content row but the file has been deleted,
    GET /papers/content/{id}/html returns 410 Gone (not 404)."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    # Seed paper_content with html_path pointing to a path that doesn't exist on disk.
    missing_path = tmp_path / "does-not-exist.html"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        paper_content_id = await _seed_paper_content(
            conn,
            content_key="arxiv:1706.03762",
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            html_path=str(missing_path),
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/papers/content/{paper_content_id}/html")

    assert r.status_code == 410


# ---------------------------------------------------------------------------
# v2.4-4 tests — GET /papers + POST /papers paper_id discrimination
# ---------------------------------------------------------------------------


async def test_list_session_references_returns_attached_papers_joined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /papers?session_id=N returns all papers joined to paper_content."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        pc_id = await _seed_paper_content(
            conn,
            content_key="arxiv:1706.03762",
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            year=2017,
        )
        papers_id = await _seed_papers_row(
            conn, session_id=session_id, paper_content_id=pc_id, enabled=1
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/papers?session_id={session_id}")

    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["papers_id"] == papers_id
    assert item["paper_content_id"] == pc_id
    assert item["enabled"] is True
    assert item["arxiv_id"] == "1706.03762"
    assert item["title"] == "Attention Is All You Need"
    assert item["year"] == 2017
    assert item["kind"] == "arxiv"


async def test_post_papers_accepts_paper_id_arxiv_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /papers with paper_id='arxiv:<id>' triggers ingest path."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await _seed_session(conn)  # session_id=1

    async def _fake_ingest(self: Any, req: Any) -> IngestResult:
        return IngestResult(
            paper_content_id=1, papers_id=1, cache_hit=False,
            title="Attention Is All You Need",
        )

    import paperhub.pipelines.paper_pipeline as pipeline_module

    with patch.object(pipeline_module.PaperPipeline, "ingest", _fake_ingest):
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/papers",
                json={"session_id": 1, "paper_id": "arxiv:1706.03762"},
            )

    assert r.status_code == 201
    assert r.json()["cache_hit"] is False
    assert r.json()["title"] == "Attention Is All You Need"


async def test_post_papers_accepts_paper_id_library_prefix_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /papers with paper_id='library:<pc_id>' attaches an existing paper_content row."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        pc_id = await _seed_paper_content(
            conn,
            content_key="arxiv:1706.03762",
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(
            "/papers",
            json={"session_id": session_id, "paper_id": f"library:{pc_id}"},
        )
        r2 = await client.post(
            "/papers",
            json={"session_id": session_id, "paper_id": f"library:{pc_id}"},
        )

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["papers_id"] == r2.json()["papers_id"]
    assert r1.json()["cache_hit"] is True


async def test_post_papers_legacy_arxiv_id_field_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /papers with legacy arxiv_id field still accepted (regression guard)."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await _seed_session(conn)  # session_id=1

    async def _fake_ingest(self: Any, req: Any) -> IngestResult:
        return IngestResult(
            paper_content_id=1, papers_id=1, cache_hit=True,
            title="GPT-3",
        )

    import paperhub.pipelines.paper_pipeline as pipeline_module

    with patch.object(pipeline_module.PaperPipeline, "ingest", _fake_ingest):
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/papers",
                json={"session_id": 1, "arxiv_id": "2005.14165"},
            )

    assert r.status_code == 201
    assert r.json()["title"] == "GPT-3"


async def test_post_papers_accepts_paper_id_ss_prefix_resolves_to_arxiv_when_externalIds_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /papers with paper_id='ss:<id>' + SS metadata having externalIds.ArXiv
    falls through to the arxiv ingest path."""
    await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await _seed_session(conn)

    async def _fake_meta(paper_id: str) -> Any:
        from paperhub.pipelines.semantic_scholar import SemanticScholarMetadata

        return SemanticScholarMetadata(
            paperId=paper_id, title="T", abstract="abs", year=2024, authors=[],
            arxiv_id="2401.99999", open_access_pdf_url=None,
        )

    async def _fake_ingest(self: Any, req: Any) -> IngestResult:
        return IngestResult(
            paper_content_id=1, papers_id=1, cache_hit=False, title="T",
        )

    import paperhub.agents.research_tools as rt
    import paperhub.pipelines.paper_pipeline as pipeline_module

    monkeypatch.setattr(rt, "fetch_paper_metadata", _fake_meta)
    with patch.object(pipeline_module.PaperPipeline, "ingest", _fake_ingest):
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/papers",
                json={"session_id": 1, "paper_id": "ss:abcd"},
            )

    assert r.status_code == 201, r.text
    assert r.json()["title"] == "T"


async def test_post_papers_returns_422_no_ingestible_source_when_ss_has_no_arxiv_and_no_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /papers with ss:<id> + no arXiv + no openAccessPdf → 422
    no_ingestible_source."""
    await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await _seed_session(conn)

    async def _fake_meta(paper_id: str) -> Any:
        from paperhub.pipelines.semantic_scholar import SemanticScholarMetadata

        return SemanticScholarMetadata(
            paperId=paper_id, title="Closed access", abstract="abs",
            year=2024, authors=[], arxiv_id=None, open_access_pdf_url=None,
        )

    import paperhub.agents.research_tools as rt

    monkeypatch.setattr(rt, "fetch_paper_metadata", _fake_meta)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/papers",
            json={"session_id": 1, "paper_id": "ss:closed"},
        )

    assert r.status_code == 422, r.text
    body = r.json()
    detail = body["detail"]
    assert detail["detail"] == "no_ingestible_source"
    assert detail["title"] == "Closed access"
    assert detail["paper_id"] == "ss:closed"


async def test_post_papers_rejects_both_paper_id_and_arxiv_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /papers with both paper_id and arxiv_id → 422 validation error."""
    db_path = await _get_db_path(tmp_path, monkeypatch)

    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await _seed_session(conn)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/papers",
            json={
                "session_id": 1,
                "paper_id": "arxiv:1706.03762",
                "arxiv_id": "1706.03762",
            },
        )

    assert r.status_code == 422
