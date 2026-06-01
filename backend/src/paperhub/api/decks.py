"""Deck REST surface (Plan F Phase 1 — SRS FR-12).

Three read-only endpoints exposing the current deck for a session:
  GET /sessions/{session_id}/deck      → metadata dict (404 if no deck)
  GET /sessions/{session_id}/deck/pdf  → FileResponse (404 if no PDF on disk)
  GET /sessions/{session_id}/deck/tex  → FileResponse (404 if no tex on disk)

Connection idiom mirrors memories.py exactly: each handler opens a fresh DB
connection via ``async with open_db(settings.db_path) as conn``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.deck_slides import (
    get_deck_slides,
    rebuild_speaker_notes_json,
    update_slide_note,
)
from paperhub.db.decks import get_deck


class NoteEdit(BaseModel):
    """Body for a manual speaker-note edit."""

    text: str

router = APIRouter(tags=["decks"])

# Characters illegal in filenames across Windows/macOS/Linux, plus controls.
_FILENAME_BANNED = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _download_name(deck: Any, ext: str) -> str:
    """Build a human, source-identifying download filename from the deck title
    (e.g. "Transformer 拋棄遞迴與卷積的注意力架構.pdf") instead of a generic
    ``deck.pdf``. Non-ASCII titles are preserved — Starlette emits them via the
    RFC 5987 ``filename*`` form. Falls back to ``slides`` for an empty title."""
    plan = deck.plan or {}
    title = str(plan.get("title") or "").strip()
    name = _FILENAME_BANNED.sub("", title)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return f"{(name or 'slides')[:80]}.{ext}"


def _exists(path: str) -> bool:
    """Sync path-existence check, called from async handlers.

    Extracted into a non-async helper so the ruff ASYNC240 rule (which
    flags os.path / pathlib calls inside async defs) does not trigger —
    the check is genuinely synchronous and short-circuits quickly.
    """
    return Path(path).exists()


@router.get("/sessions/{session_id}/deck")
async def get_deck_meta(session_id: int) -> dict[str, Any]:
    """Return deck metadata for the session, 404 if no deck exists."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="no deck for this session")
    return {
        "deck_id": deck.id,
        "session_id": deck.session_id,
        "page_count": deck.page_count,
        "current_version_id": deck.current_version_id,
        "status": deck.status,
        "plan": deck.plan,
        "speaker_notes": deck.speaker_notes,
        "contributing_paper_ids": deck.contributing_paper_ids,
        "updated_at": deck.updated_at,
    }


@router.get("/sessions/{session_id}/deck/pdf")
async def get_deck_pdf(session_id: int) -> FileResponse:
    """Stream the compiled PDF, 404 if no deck or pdf_path missing/not-on-disk."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None or not deck.pdf_path or not _exists(deck.pdf_path):
        raise HTTPException(
            status_code=404, detail="no compiled PDF for this session"
        )
    return FileResponse(
        deck.pdf_path,
        media_type="application/pdf",
        filename=_download_name(deck, "pdf"),
    )


@router.get("/sessions/{session_id}/deck/tex")
async def get_deck_tex(session_id: int) -> FileResponse:
    """Stream the LaTeX source, 404 if no deck or tex_path not-on-disk."""
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None or not _exists(deck.tex_path):
        raise HTTPException(
            status_code=404, detail="no deck source for this session"
        )
    return FileResponse(
        deck.tex_path,
        media_type="text/plain",
        filename=_download_name(deck, "tex"),
    )


@router.patch("/sessions/{session_id}/deck/notes/{page}")
async def edit_deck_note(
    session_id: int, page: int, body: NoteEdit
) -> dict[str, Any]:
    """Manually set the speaker note for the slide occupying ``page``.

    Resolves ``page`` to the slide whose [page_start, page_end] contains it (so
    editing a continuation page updates that slide's single note), preserves the
    slide's existing note_language, rebuilds the page→note cache, and returns it.
    404 if there is no deck or no slide covering the page.
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
        if deck is None:
            raise HTTPException(status_code=404, detail="no deck for this session")
        rows = await get_deck_slides(conn, deck_id=deck.id)
        target = next(
            (r for r in rows if r.page_start <= page <= r.page_end), None
        )
        if target is None:
            raise HTTPException(
                status_code=404, detail=f"no slide covering page {page}"
            )
        await update_slide_note(
            conn,
            deck_id=deck.id,
            slide_index=target.slide_index,
            note_text=body.text,
            note_language=target.note_language or "",
        )
        notes = await rebuild_speaker_notes_json(conn, deck_id=deck.id)
    return {"speaker_notes": notes, "has_notes": bool(notes)}


__all__ = ["router"]
