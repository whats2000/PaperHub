"""Tests for the deck REST surface (Plan F Task 11).

Exercises:
  GET /sessions/{id}/deck       → 404 when no deck; metadata dict when present
  GET /sessions/{id}/deck/pdf   → 404 when no deck; PDF bytes when present
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.db.deck_slides import DeckSlideInput, replace_deck_slides
from paperhub.db.decks import get_deck, upsert_deck

pytestmark = pytest.mark.asyncio


async def _seed_deck_with_slides(conn: aiosqlite.Connection, tmp_path: Path) -> int:
    """Seed a session + deck + two deck_slides (slide 1 spans pages 2-3).
    Returns the deck_id."""
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    await upsert_deck(
        conn, session_id=1, run_id=None,
        tex_path=str(tmp_path / "deck.tex"), pdf_path=str(pdf),
        speaker_notes={}, plan={"title": "T"}, page_count=3, theme="metropolis",
        contributing_paper_ids=[], status="ok",
    )
    deck = await get_deck(conn, session_id=1)
    assert deck is not None
    await replace_deck_slides(
        conn, deck_id=deck.id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="f0", page_start=1, page_end=1,
                           note_text="orig 0", note_language="English"),
            DeckSlideInput(slide_index=1, frame_tex="f1", page_start=2, page_end=3,
                           note_text="orig 1", note_language="English"),
        ],
    )
    return deck.id


async def test_get_deck_404_when_none(
    app_with_db: tuple[Any, aiosqlite.Connection],
) -> None:
    """GET /sessions/1/deck with no deck row → 404."""
    app, _ = app_with_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/sessions/1/deck")
        assert r.status_code == 404


async def test_get_deck_and_pdf(
    app_with_db: tuple[Any, aiosqlite.Connection],
    tmp_path: Path,
) -> None:
    """Seed a session + deck + on-disk PDF; verify metadata + PDF endpoints."""
    app, conn = app_with_db

    # Seed the session row (FK required by decks).
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()

    # Write a fake PDF to disk.
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    await upsert_deck(
        conn,
        session_id=1,
        run_id=None,
        tex_path=str(tmp_path / "deck.tex"),
        pdf_path=str(pdf),
        speaker_notes={"1": "n"},
        plan={},
        page_count=1,
        theme="metropolis",
        contributing_paper_ids=[],
        status="ok",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        # Metadata endpoint.
        meta_resp = await c.get("/sessions/1/deck")
        assert meta_resp.status_code == 200
        meta = meta_resp.json()
        assert meta["page_count"] == 1
        assert meta["speaker_notes"] == {"1": "n"}

        # PDF endpoint.
        pdf_resp = await c.get("/sessions/1/deck/pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.content.startswith(b"%PDF")


async def test_download_filename_derived_from_title(
    app_with_db: tuple[Any, aiosqlite.Connection],
    tmp_path: Path,
) -> None:
    """The PDF/TeX downloads must be named after the deck title (so the user
    can tell which paper they came from), not a generic ``deck.pdf``. Illegal
    filename chars are stripped; an empty title falls back to ``slides``."""
    app, conn = app_with_db
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()

    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    tex = tmp_path / "deck.tex"
    tex.write_text("\\documentclass{beamer}")

    await upsert_deck(
        conn,
        session_id=1,
        run_id=None,
        tex_path=str(tex),
        pdf_path=str(pdf),
        speaker_notes={},
        # Title carries an illegal ':' that must be stripped from the filename.
        plan={"title": "Attention: Is All You Need"},
        page_count=1,
        theme="metropolis",
        contributing_paper_ids=[],
        status="ok",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        # Starlette percent-encodes names with spaces (filename*=utf-8''…),
        # so decode before asserting on the readable form.
        pdf_resp = await c.get("/sessions/1/deck/pdf")
        cd = unquote(pdf_resp.headers["content-disposition"])
        assert "deck.pdf" not in cd
        assert "Attention Is All You Need.pdf" in cd  # ':' stripped

        tex_resp = await c.get("/sessions/1/deck/tex")
        assert "Attention Is All You Need.tex" in unquote(
            tex_resp.headers["content-disposition"]
        )


async def test_patch_note_updates_slide_and_rebuilds_map(
    app_with_db: tuple[Any, aiosqlite.Connection],
    tmp_path: Path,
) -> None:
    """PATCH /deck/notes/{page} updates the owning slide's note and returns the
    rebuilt page→note map."""
    app, conn = app_with_db
    await _seed_deck_with_slides(conn, tmp_path)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.patch("/sessions/1/deck/notes/1", json={"text": "my edited note"})
        assert r.status_code == 200
        body = r.json()
        assert body["speaker_notes"]["1"] == "my edited note"
        assert body["has_notes"] is True
        # Slide 0's note changed; slide 1 (pages 2-3) untouched.
        assert body["speaker_notes"]["2"] == "orig 1"
        assert body["speaker_notes"]["3"] == "(continued)"


async def test_patch_note_on_continued_page_targets_owning_slide(
    app_with_db: tuple[Any, aiosqlite.Connection],
    tmp_path: Path,
) -> None:
    """Editing via page 3 (a continuation of the slide spanning 2-3) updates
    that slide's single note, shown on its first page (2)."""
    app, conn = app_with_db
    await _seed_deck_with_slides(conn, tmp_path)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.patch("/sessions/1/deck/notes/3", json={"text": "edited via continued"})
        assert r.status_code == 200
        notes = r.json()["speaker_notes"]
        assert notes["2"] == "edited via continued"  # owning slide's first page
        assert notes["3"] == "(continued)"


async def test_patch_note_404_when_no_deck_or_bad_page(
    app_with_db: tuple[Any, aiosqlite.Connection],
    tmp_path: Path,
) -> None:
    app, conn = app_with_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        # No deck at all.
        r = await c.patch("/sessions/1/deck/notes/1", json={"text": "x"})
        assert r.status_code == 404

    await _seed_deck_with_slides(conn, tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        # Page out of range.
        r = await c.patch("/sessions/1/deck/notes/99", json={"text": "x"})
        assert r.status_code == 404
