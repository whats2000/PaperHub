"""Tests for the chunk-resolution endpoint (Plan D, FR-03 Citation Canvas)."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema


async def _seed_chunk(
    conn: aiosqlite.Connection,
    *,
    paper_content_id: int = 1,
    section: str | None = "3.2 Routing",
    text: str = "Expert collapse is mitigated by load balancing.",
) -> int:
    # paper_content has NOT-NULL columns + a CHECK that exactly one of
    # arxiv_id / sha256 is set; seed a minimal valid row first.
    await conn.execute(
        "INSERT OR IGNORE INTO paper_content "
        "(id, content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        " source_path, source_dir_path, html_path) "
        "VALUES (?, ?, 'arxiv', ?, ?, '[]', 2024, '', '/tmp/s.tex', '/tmp', '/tmp/s.html')",
        (paper_content_id, f"arxiv:test-{paper_content_id}", f"test-{paper_content_id}",
         "Test Paper"),
    )
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
        "VALUES (?, ?, 0, ?, ?)",
        (paper_content_id, section, len(text), text),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_get_chunk_returns_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        chunk_id = await _seed_chunk(conn)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/chunks/{chunk_id}")

    assert r.status_code == 200
    body = r.json()
    assert body == {
        "id": chunk_id,
        "paper_content_id": 1,
        "section": "3.2 Routing",
        "text": "Expert collapse is mitigated by load balancing.",
        "dom_id": None,
        "match_text": None,
    }


async def test_get_chunk_null_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """section is the only nullable field in ChunkResolution; verify it serialises
    as JSON null (not the string "None") when the DB row has section=NULL."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        chunk_id = await _seed_chunk(conn, paper_content_id=2, section=None)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/chunks/{chunk_id}")

    assert r.status_code == 200
    assert r.json()["section"] is None


async def test_get_chunk_404_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/chunks/99999")

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# A1: match_text field in the API response
# ---------------------------------------------------------------------------


async def _seed_chunk_with_match_text(
    conn: aiosqlite.Connection,
    *,
    paper_content_id: int = 10,
    text: str = "Some chunk text with **markdown**.",
    match_text: str | None = "Some chunk text with markdown.",
) -> int:
    """Seed a chunk row that includes match_text (F2.1 A1)."""
    await conn.execute(
        "INSERT OR IGNORE INTO paper_content "
        "(id, content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        " source_path, source_dir_path, html_path) "
        "VALUES (?, ?, 'arxiv', ?, ?, '[]', 2024, '', '/tmp/s.tex', '/tmp', '/tmp/s.html')",
        (paper_content_id, f"arxiv:mt-{paper_content_id}", f"mt-{paper_content_id}",
         "Match Text Paper"),
    )
    await conn.execute(
        "INSERT INTO chunks "
        "(paper_content_id, section, char_start, char_end, text, match_text) "
        "VALUES (?, ?, 0, ?, ?, ?)",
        (paper_content_id, "Introduction", len(text), text, match_text),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_get_chunk_exposes_match_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chunks endpoint must include match_text in its JSON response (F2.1 A1)."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        chunk_id = await _seed_chunk_with_match_text(
            conn,
            text="Hello **world**.",
            match_text="Hello world.",
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/chunks/{chunk_id}")

    assert r.status_code == 200
    body = r.json()
    assert "match_text" in body
    assert body["match_text"] == "Hello world."


async def test_get_chunk_match_text_null_when_not_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """match_text is NULL by default; the endpoint must return match_text: null."""
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        chunk_id = await _seed_chunk_with_match_text(
            conn,
            paper_content_id=11,
            text="Plain text chunk.",
            match_text=None,
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/chunks/{chunk_id}")

    assert r.status_code == 200
    body = r.json()
    assert "match_text" in body
    assert body["match_text"] is None
