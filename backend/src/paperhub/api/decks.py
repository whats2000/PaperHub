"""Deck REST surface (Plan F Phase 1 — SRS FR-12; F4.5 version-history endpoints).

Read-only endpoints exposing the current deck for a session:
  GET  /sessions/{session_id}/deck                          → metadata dict (404 if no deck)
  GET  /sessions/{session_id}/deck/pdf                      → FileResponse (404 if no PDF on disk)
  GET  /sessions/{session_id}/deck/tex                      → FileResponse (404 if no tex on disk)
  GET  /sessions/{session_id}/deck/versions                 → version-snapshot list (F4.5)
  POST /sessions/{session_id}/deck/versions/{file}/restore  → restore snapshot, bump current_version_id (F4.5)

Connection idiom mirrors memories.py exactly: each handler opens a fresh DB
connection via ``async with open_db(settings.db_path) as conn``.
"""
from __future__ import annotations

import asyncio
import json
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
    replace_deck_slides,
    update_slide_note,
)
from paperhub.db.decks import get_deck
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
)
from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides
from paperhub.pipelines.slide_pipeline.history import VersionHistory


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


# ── F4.5 version history ─────────────────────────────────────────────────
# A version snapshot lives at
#   <workspace>/chat_session/<sid>/slides/edit_history/<version_id>.json
# stamped by ``sl_emit`` after a successful compile. The version_id is the
# filename stem (e.g. ``version_20260601_120000_000000``); ``decks.current_version_id``
# stores exactly that stem. The two endpoints below let the frontend list +
# restore snapshots.

# A version_id MUST look like ``version_<digits>_<digits>[_<digits>]``. The
# strict shape blocks path-traversal in the restore endpoint (``..``, slashes,
# backslashes all rejected) without resorting to ad-hoc string scrubbing.
_VERSION_ID_RE = re.compile(r"^version_\d{8}_\d{6}(?:_\d+)?$")


def _slides_workdir(session_id: int) -> Path:
    """Resolve the slides workdir for a session (mirrors report_graph.py)."""
    settings = load_settings()
    return settings.workspace_dir / "chat_session" / str(session_id) / "slides"


def _count_frames(tex_content: str) -> int:
    """Best-effort page-count derived from the snapshot's beamer source.

    Each ``\\begin{frame} … \\end{frame}`` produces one PDF page (overlay
    expansion happens at compile time, which we don't replay here). Returns 0
    on parse failure rather than raising — the version list is informational.
    """
    try:
        frames = extract_frames_from_beamer(tex_content)
    except Exception:
        return 0
    # ``extract_frames_from_beamer`` already returns one row per page
    # (overlay expansion); collapse to unique frame bodies for the logical
    # frame count. Fall back to the raw row count if the structure is
    # unexpected.
    if not frames:
        return 0
    seen: set[str] = set()
    for entry in frames:
        try:
            seen.add(entry[1])
        except Exception:
            return len(frames)
    return len(seen)


def _read_snapshot(path: Path) -> dict[str, Any] | None:
    """Read + JSON-parse one version snapshot file, returning None on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except Exception:
        return None


@router.get("/sessions/{session_id}/deck/versions")
async def list_deck_versions(session_id: int) -> list[dict[str, Any]]:
    """List every ``edit_history/version_*.json`` snapshot for the session.

    Returns one entry per snapshot — newest first by recorded timestamp — with
    enough metadata for the frontend's card list:

    ``{version_id, timestamp, description, page_count, is_active}``

    ``is_active`` flags the snapshot whose filename stem equals
    ``decks.current_version_id``. 404 if there is no deck row for the session.
    A missing ``edit_history/`` directory returns ``[]`` (a deck row may exist
    before its first snapshot lands in the rare race).
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="no deck for this session")

    edit_history = _slides_workdir(session_id) / "edit_history"

    def _scan() -> list[dict[str, Any]]:
        if not edit_history.exists():
            return []
        entries: list[dict[str, Any]] = []
        for fp in edit_history.glob("version_*.json"):
            data = _read_snapshot(fp)
            if data is None:
                continue
            version_id = fp.stem
            entries.append({
                "version_id": version_id,
                "timestamp": str(data.get("timestamp") or ""),
                "description": str(data.get("description") or ""),
                "page_count": _count_frames(str(data.get("tex_content") or "")),
                "is_active": version_id == (deck.current_version_id or ""),
            })
        # Newest first: timestamps are ISO-8601-ish ``YYYY-MM-DDTHH:MM:SS`` or
        # the ``YYYYMMDD_HHMMSS_micros`` stamped by sl_emit. Both sort
        # lexicographically in the right order; fall back to version_id (which
        # embeds the same digits) when the timestamp field is empty.
        entries.sort(
            key=lambda e: (e["timestamp"] or e["version_id"]),
            reverse=True,
        )
        return entries

    return await asyncio.to_thread(_scan)


@router.post("/sessions/{session_id}/deck/versions/{version_id}/restore")
async def restore_deck_version(
    session_id: int, version_id: str
) -> dict[str, Any]:
    """Restore the given snapshot to ``slides/deck.tex`` + ``speaker_notes.json``
    AND bump ``decks.current_version_id`` to point at it.

    The restored tex/notes are written via ``VersionHistory.restore_version``
    (the same machinery ``sl_emit`` snapshots through), then the DB row is
    updated in one statement. Returns the new ``current_version_id`` so the
    frontend doesn't have to re-fetch the deck.

    Errors:
      * 400 — ``version_id`` doesn't match ``version_\\d{8}_\\d{6}(_\\d+)?``
        (path-traversal guard).
      * 404 — no deck row, no slides workdir, or no snapshot file by that name.
    """
    if not _VERSION_ID_RE.match(version_id):
        raise HTTPException(status_code=400, detail="invalid version_id")

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
        if deck is None:
            raise HTTPException(status_code=404, detail="no deck for this session")

        slides_dir = _slides_workdir(session_id)
        snapshot_path = slides_dir / "edit_history" / f"{version_id}.json"

        # Read the snapshot's bundled notes BEFORE restoring deck.tex so we
        # can replay them into deck_slides after the rebuild. The DB is the
        # source of truth for the UI, so writing notes to disk via
        # restore_version alone is not enough.
        def _read_snapshot_notes() -> dict[str, str] | None:
            data = _read_snapshot(snapshot_path)
            if data is None:
                return None
            notes = data.get("speaker_notes")
            if not isinstance(notes, dict):
                return None  # absent, null, or unexpected shape → no notes
            return {str(k): str(v) for k, v in notes.items()}

        bundled_notes = await asyncio.to_thread(_read_snapshot_notes)

        def _restore() -> bool:
            if not snapshot_path.exists():
                return False
            tex_path = slides_dir / "deck.tex"
            tex_path.parent.mkdir(parents=True, exist_ok=True)
            return VersionHistory(slides_dir).restore_version(
                f"{version_id}.json", str(tex_path)
            )

        ok = await asyncio.to_thread(_restore)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"snapshot {version_id} not found or unreadable",
            )

        # Recompile so deck.pdf reflects the restored tex. The snapshot was a
        # successful compile when it was saved, so no LLM-revise is needed —
        # we pass an identity revise callback and the standard 2 attempts
        # (the first run resolves \tableofcontents-style refs; the second
        # produces a final, ref-stable PDF).
        async def _identity_revise(_log: str, tex: str) -> str:
            return tex

        restored_tex = await asyncio.to_thread(
            (slides_dir / "deck.tex").read_text, encoding="utf-8"
        )
        result = await compile_mod.compile_with_revise(
            tex=restored_tex,
            workdir=slides_dir,
            tex_name="deck.tex",
            revise=_identity_revise,
            max_retries=1,
        )

        # Bump current_version_id + page_count + status + pdf_path + updated_at
        # in one statement so a concurrent GET /deck sees the new state
        # atomically. A failed recompile is unexpected (the snapshot used to
        # build cleanly) but we still flip current_version_id so the chip on
        # screen matches the source — status=error surfaces the failure.
        pdf_path = slides_dir / "deck.pdf"
        new_pdf_path = str(pdf_path) if result.ok and pdf_path.exists() else None
        await conn.execute(
            """
            UPDATE decks SET
                current_version_id = ?,
                page_count = ?,
                pdf_path = ?,
                status = ?,
                updated_at = datetime('now')
            WHERE session_id = ?
            """,
            (
                version_id,
                result.page_count if result.ok else 0,
                new_pdf_path,
                "ok" if result.ok else "error",
                session_id,
            ),
        )
        # Refresh deck_slides so per-page navigation + speaker-note lookup
        # match the restored frame list (a previous edit could have changed
        # frame count), then REAPPLY the snapshot's bundled speaker_notes
        # by slide_index. The DB is what the SlidesPanel reads, so a
        # restored version isn't truly restored until note_text rows match
        # the snapshot's per-page mapping. ``bundled_notes`` keys are
        # stringified slide_index ints (matching how sl_emit / the
        # in-place notes patch write them).
        fresh = await get_deck(conn, session_id=session_id)
        if fresh is not None and result.ok:
            await replace_deck_slides(
                conn,
                deck_id=fresh.id,
                slides=build_deck_slides(result.tex, result.page_count),
            )
            if bundled_notes:
                for r in await get_deck_slides(conn, deck_id=fresh.id):
                    nt = bundled_notes.get(str(r.slide_index))
                    if nt:
                        await update_slide_note(
                            conn,
                            deck_id=fresh.id,
                            slide_index=r.slide_index,
                            note_text=nt,
                            note_language="",
                        )
            await rebuild_speaker_notes_json(conn, deck_id=fresh.id)
        await conn.commit()

    return {
        "ok": True,
        "current_version_id": version_id,
        "page_count": result.page_count if result.ok else 0,
        "status": "ok" if result.ok else "error",
    }


__all__ = ["router"]
