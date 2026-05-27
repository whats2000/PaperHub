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

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.decks import get_deck

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
        "theme": deck.theme,
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


__all__ = ["router"]
