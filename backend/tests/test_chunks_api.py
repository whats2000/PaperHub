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
    }


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
