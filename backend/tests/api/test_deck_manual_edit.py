"""F6.2 — manual slide editing + per-slide source exposure.

Covers:
  * GET  /sessions/{id}/deck/slides            (Task 2 — per-slide detail)
  * PUT  /sessions/{id}/deck/slides/{page}/tex (Task 3 — frame edit + recompile)
  * PUT  /sessions/{id}/deck/tex               (Task 4 — whole-deck edit)

The recompile entrypoint (``compile_mod.compile_with_revise``) is patched so the
suite never needs a real pdflatex (CI ships no TeX Live).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.pipelines.slide_pipeline.compile import CompileResult

_HDR = {"X-Paperhub-Session-Id": "1"}

_FRAME_A = "\\begin{frame}{Title A}\nFirst frame body.\n\\end{frame}"
_FRAME_B = "\\begin{frame}{Title B}\nSecond frame body.\n\\end{frame}"
_DECK_TEX = (
    "\\documentclass{beamer}\n\\begin{document}\n"
    f"{_FRAME_A}\n\n% cite: 7:Introduction\n{_FRAME_B}\n"
    "\\end{document}\n"
)


async def _seed_session(conn: aiosqlite.Connection, session_id: int) -> None:
    await conn.execute(
        "INSERT INTO chat_sessions (id, created_at, title) "
        "VALUES (?, datetime('now'), 't')",
        (session_id,),
    )
    await conn.commit()


async def _seed_deck_with_slides(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    slides_dir: Path,
    page_count: int = 2,
) -> int:
    """Insert a deck + two deck_slides rows; return the deck id."""
    tex_path = slides_dir / "deck.tex"
    await conn.execute(
        "INSERT INTO decks (session_id, tex_path, pdf_path, page_count, "
        "current_version_id, contributing_paper_ids_json, status) "
        "VALUES (?, ?, ?, ?, ?, '[]', 'ok')",
        (session_id, str(tex_path), str(slides_dir / "deck.pdf"),
         page_count, "version_seed"),
    )
    async with conn.execute(
        "SELECT id FROM decks WHERE session_id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    deck_id = int(row[0])
    await conn.executemany(
        "INSERT INTO deck_slides (deck_id, slide_index, frame_tex, note_text, "
        "note_language, page_start, page_end, source_sections_json) "
        "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
        [
            (deck_id, 0, _FRAME_A, "note A", 1, 1,
             '[{"paper_id": 7, "section_name": "Introduction", '
             '"chunk_ids": [101, 102]}]'),
            (deck_id, 1, _FRAME_B, None, 2, 2, "[]"),
        ],
    )
    await conn.commit()
    return deck_id


# ── Task 2: GET /deck/slides ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_deck_slides_returns_frame_and_parsed_sources(
    tmp_path: Path, app_with_db: tuple[Any, aiosqlite.Connection]
) -> None:
    app, conn = app_with_db
    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    slides_dir.mkdir(parents=True)
    await _seed_session(conn, 1)
    await _seed_deck_with_slides(conn, session_id=1, slides_dir=slides_dir)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/sessions/1/deck/slides", headers=_HDR)

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["slide_index"] for r in rows] == [0, 1]
    assert rows[0]["frame_tex"] == _FRAME_A
    assert rows[0]["page_start"] == 1 and rows[0]["page_end"] == 1
    # source_sections is PARSED (a list of dicts), not the raw JSON string.
    assert rows[0]["source_sections"] == [
        {"paper_id": 7, "section_name": "Introduction", "chunk_ids": [101, 102]}
    ]
    assert rows[1]["source_sections"] == []


@pytest.mark.asyncio
async def test_get_deck_slides_404_without_deck(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    app, conn = app_with_db
    await _seed_session(conn, 1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/sessions/1/deck/slides", headers=_HDR)
    assert resp.status_code == 404
