"""Tests for POST /papers/upload — multipart PDF ingest endpoint (v2.9-1).

Mirrors the test_papers_api.py pattern: per-test isolated DB via tmp_path +
PAPERHUB_WORKSPACE monkeypatch + create_app(), so no shared state across
tests. The pipeline is exercised end-to-end with a real 1-page sample PDF
fixture (the in-process embedder + reranker are activated by conftest's
PAPERHUB_INPROCESS_MODELS=1 default).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema


async def _seed_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _setup_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    """Point PAPERHUB_WORKSPACE at tmp_path, migrate the DB, seed a session.

    Returns the seeded session_id.
    """
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        return await _seed_session(conn)


@pytest.mark.asyncio
async def test_upload_pdf_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cache_hit"] is False
    assert body["paper_content_id"] >= 1
    assert body["papers_id"] >= 1
    assert body["title"] == "sample"  # upload_path.stem fallback per pipeline


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/papers/upload",
            data={"session_id": str(session_id)},
            files={"file": ("a.txt", b"hello", "text/plain")},
        )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("PAPERHUB_MAX_UPLOAD_MB", "1")
    app = create_app()
    transport = ASGITransport(app=app)
    big = b"%PDF-1.4\n" + b"\x00" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/papers/upload",
            data={"session_id": str(session_id)},
            files={"file": ("big.pdf", big, "application/pdf")},
        )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_upload_sanitises_path_traversal_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client-supplied ``../../../etc/passwd.pdf`` must be collapsed to
    ``passwd.pdf`` by ``Path(...).name``; the file lands inside the
    tempdir sandbox, and the pipeline's title fallback uses the
    sanitised stem (``passwd``). Pins the existing sandbox behaviour."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={
                    "file": (
                        "../../../etc/passwd.pdf",
                        f,
                        "application/pdf",
                    ),
                },
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "passwd"


@pytest.mark.asyncio
async def test_upload_filename_sanitises_to_empty_falls_back_to_upload_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the client-supplied filename collapses to an empty ``.name``
    under ``Path(...).name`` (e.g. ``"/"`` → ``""``), the route's
    ``or "upload.pdf"`` second fallback must kick in, yielding a
    pipeline title stem of ``upload``. Pins the defensive fallback
    that prevents an empty-filename UploadFile from writing to the
    tempdir root."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("/", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "upload"


@pytest.mark.asyncio
async def test_upload_pdf_with_title_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the caller supplies a non-empty ``title`` Form field, the
    pipeline must honour it instead of falling back to ``upload_path.stem``.
    Verifies the override flows all the way through to ``paper_content.title``
    in the DB, not just the response body."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={
                    "session_id": str(session_id),
                    "title": "Custom Title For The Paper",
                },
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Custom Title For The Paper"

    # DB-level assertion — make sure the override actually persisted.
    async with (
        aiosqlite.connect(tmp_path / "paperhub.db") as conn,
        conn.execute(
            "SELECT title FROM paper_content WHERE id = ?",
            (body["paper_content_id"],),
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "Custom Title For The Paper"


@pytest.mark.asyncio
async def test_upload_pdf_blank_title_falls_back_to_filename_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace-only ``title`` must NOT shadow the filename-stem fallback.
    Empty/blank user input is treated as "no override supplied"."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id), "title": "   "},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "sample"


@pytest.mark.asyncio
async def test_upload_pdf_no_title_field_falls_back_to_filename_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: omitting the new ``title`` Form field entirely must
    keep the existing happy-path behaviour (filename-stem fallback)."""
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            r = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "sample"


@pytest.mark.asyncio
async def test_upload_same_bytes_returns_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _setup_workspace(tmp_path, monkeypatch)
    sample_pdf = Path(__file__).parent / "fixtures" / "papers" / "sample.pdf"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with sample_pdf.open("rb") as f:
            first = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
        with sample_pdf.open("rb") as f:
            second = await ac.post(
                "/papers/upload",
                data={"session_id": str(session_id)},
                files={"file": ("sample.pdf", f, "application/pdf")},
            )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_body: dict[str, Any] = first.json()
    second_body: dict[str, Any] = second.json()
    assert second_body["cache_hit"] is True
    assert second_body["paper_content_id"] == first_body["paper_content_id"]
