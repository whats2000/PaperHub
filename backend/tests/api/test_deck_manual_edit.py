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


# ── Task 3: PUT /deck/slides/{page}/tex ──────────────────────────────────


def _ok_compile(tex: str = _DECK_TEX, page_count: int = 2) -> AsyncMock:
    """A compile_with_revise stub that 'compiles' by writing the candidate
    PDF + echoing the tex (matches the real entrypoint's deck.tex/deck.pdf
    side effects, but for the candidate tex-name)."""

    async def _impl(*, tex: str, workdir: Path, tex_name: str, **_kw: Any) -> CompileResult:
        # Mimic the real entrypoint: write the candidate pdf so _promote can
        # copy it to deck.pdf.
        (workdir / tex_name).write_text(tex, encoding="utf-8")
        (workdir / "deck_candidate.pdf").write_bytes(b"%PDF-1.4 compiled\n")
        return CompileResult(ok=True, attempts=1, tex=tex, log="", page_count=page_count)

    return AsyncMock(side_effect=_impl)


@pytest.mark.asyncio
async def test_put_frame_tex_recompiles_and_persists(
    tmp_path: Path, app_with_db: tuple[Any, aiosqlite.Connection]
) -> None:
    app, conn = app_with_db
    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "deck.tex").write_text(_DECK_TEX, encoding="utf-8")
    (slides_dir / "deck.pdf").write_bytes(b"%PDF-1.4 original\n")
    await _seed_session(conn, 1)
    await _seed_deck_with_slides(conn, session_id=1, slides_dir=slides_dir)

    new_frame = "\\begin{frame}{Title B}\nEDITED second frame.\n\\end{frame}"
    transport = ASGITransport(app=app)
    with patch(
        "paperhub.api.decks.compile_mod.compile_with_revise", _ok_compile()
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/sessions/1/deck/slides/2/tex",
                json={"frame_tex": new_frame},
                headers=_HDR,
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["status"] == "ok"
    # deck.tex now contains the edited frame; a version snapshot was written.
    assert "EDITED second frame." in (slides_dir / "deck.tex").read_text()
    snaps = list((slides_dir / "edit_history").glob("version_*.json"))
    assert len(snaps) == 1


@pytest.mark.asyncio
async def test_put_frame_tex_compile_failure_keeps_last_good(
    tmp_path: Path, app_with_db: tuple[Any, aiosqlite.Connection]
) -> None:
    app, conn = app_with_db
    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    slides_dir.mkdir(parents=True)
    good_tex = _DECK_TEX
    good_pdf = b"%PDF-1.4 last-good\n"
    (slides_dir / "deck.tex").write_text(good_tex, encoding="utf-8")
    (slides_dir / "deck.pdf").write_bytes(good_pdf)
    await _seed_session(conn, 1)
    await _seed_deck_with_slides(conn, session_id=1, slides_dir=slides_dir)

    fail = AsyncMock(
        return_value=CompileResult(
            ok=False, attempts=2, tex="(broken)", log="! Undefined control sequence.",
            page_count=0,
        )
    )
    transport = ASGITransport(app=app)
    with patch("paperhub.api.decks.compile_mod.compile_with_revise", fail):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/sessions/1/deck/slides/2/tex",
                json={"frame_tex": "\\begin{frame}{Title B}\n\\bad\n\\end{frame}"},
                headers=_HDR,
            )

    assert resp.status_code == 200, resp.text  # a compile error is a normal outcome
    body = resp.json()
    assert body["ok"] is False and body["status"] == "error"
    assert "Undefined control sequence" in body["log"]
    # last-good deck.tex / deck.pdf are byte-for-byte unchanged.
    assert (slides_dir / "deck.tex").read_text() == good_tex
    assert (slides_dir / "deck.pdf").read_bytes() == good_pdf


@pytest.mark.asyncio
async def test_put_frame_tex_resolves_continuation_page(
    tmp_path: Path, app_with_db: tuple[Any, aiosqlite.Connection]
) -> None:
    """Editing a continuation page targets the owning slide's frame."""
    app, conn = app_with_db
    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "deck.tex").write_text(_DECK_TEX, encoding="utf-8")
    (slides_dir / "deck.pdf").write_bytes(b"%PDF\n")
    await _seed_session(conn, 1)
    deck_id = await _seed_deck_with_slides(conn, session_id=1, slides_dir=slides_dir)
    # Widen slide 0 to span pages 1-2 so page 2 is its continuation; slide 1
    # moves to page 3. (Two rows now overlap on page intent only for the test.)
    await conn.execute(
        "UPDATE deck_slides SET page_end = 2 WHERE deck_id = ? AND slide_index = 0",
        (deck_id,),
    )
    await conn.execute(
        "UPDATE deck_slides SET page_start = 3, page_end = 3 WHERE deck_id = ? "
        "AND slide_index = 1",
        (deck_id,),
    )
    await conn.commit()

    new_frame = "\\begin{frame}{Title A}\nEDITED owner via continuation.\n\\end{frame}"
    transport = ASGITransport(app=app)
    with patch("paperhub.api.decks.compile_mod.compile_with_revise", _ok_compile()):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/sessions/1/deck/slides/2/tex",  # page 2 == slide 0's continuation
                json={"frame_tex": new_frame},
                headers=_HDR,
            )
    assert resp.status_code == 200, resp.text
    assert "EDITED owner via continuation." in (slides_dir / "deck.tex").read_text()


@pytest.mark.asyncio
async def test_put_frame_tex_404_when_page_uncovered(
    tmp_path: Path, app_with_db: tuple[Any, aiosqlite.Connection]
) -> None:
    app, conn = app_with_db
    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "deck.tex").write_text(_DECK_TEX, encoding="utf-8")
    await _seed_session(conn, 1)
    await _seed_deck_with_slides(conn, session_id=1, slides_dir=slides_dir)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/sessions/1/deck/slides/99/tex",
            json={"frame_tex": "x"},
            headers=_HDR,
        )
    assert resp.status_code == 404


# ── Task 4: PUT /deck/tex ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_deck_tex_recompiles_whole_deck(
    tmp_path: Path, app_with_db: tuple[Any, aiosqlite.Connection]
) -> None:
    app, conn = app_with_db
    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "deck.tex").write_text(_DECK_TEX, encoding="utf-8")
    (slides_dir / "deck.pdf").write_bytes(b"%PDF original\n")
    await _seed_session(conn, 1)
    await _seed_deck_with_slides(conn, session_id=1, slides_dir=slides_dir)

    new_deck = _DECK_TEX.replace("First frame body.", "WHOLE-DECK edited body.")
    transport = ASGITransport(app=app)
    with patch(
        "paperhub.api.decks.compile_mod.compile_with_revise", _ok_compile(new_deck)
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/sessions/1/deck/tex", json={"tex": new_deck}, headers=_HDR
            )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "WHOLE-DECK edited body." in (slides_dir / "deck.tex").read_text()


@pytest.mark.asyncio
async def test_put_deck_tex_rejects_empty(
    tmp_path: Path, app_with_db: tuple[Any, aiosqlite.Connection]
) -> None:
    app, conn = app_with_db
    slides_dir = tmp_path / "chat_session" / "1" / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "deck.tex").write_text(_DECK_TEX, encoding="utf-8")
    await _seed_session(conn, 1)
    await _seed_deck_with_slides(conn, session_id=1, slides_dir=slides_dir)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/sessions/1/deck/tex", json={"tex": "   "}, headers=_HDR
        )
    assert resp.status_code == 400
