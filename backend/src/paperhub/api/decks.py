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
import contextlib
import dataclasses
import json
import re
import shutil
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from paperhub.agents.sl_cite import serialize_cite, with_grounding
from paperhub.agents.sl_read import read_section_chunks
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.deck_slides import (
    get_deck_slides,
    rebuild_speaker_notes_json,
    replace_deck_slides,
    update_slide_grounding,
    update_slide_note,
)
from paperhub.db.decks import get_deck, upsert_deck
from paperhub.models.slide_domain import SourceSection
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
)
from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides
from paperhub.pipelines.slide_pipeline.frame_splice import (
    set_frame_cite_marker,
    splice_frame,
    strip_cite,
)
from paperhub.pipelines.slide_pipeline.history import VersionHistory


class NoteEdit(BaseModel):
    """Body for a manual speaker-note edit."""

    text: str


class SlideSourceInput(BaseModel):
    """One (paper, section) the user grounds a slide to."""

    paper_id: int
    section_name: str


class SlideSourcesEdit(BaseModel):
    """Body for the structured per-slide source (grounding) editor."""

    sources: list[SlideSourceInput]


class FrameEdit(BaseModel):
    """Body for a manual single-frame LaTeX edit."""

    frame_tex: str


class DeckEdit(BaseModel):
    """Body for a manual whole-deck LaTeX edit."""

    tex: str

router = APIRouter(tags=["decks"])

# Characters illegal in filenames across Windows/macOS/Linux, plus controls.
_FILENAME_BANNED = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _read_title_from_tex(
    tex_path: str | None = None, *, tex_content: str | None = None
) -> str:
    """Return the balanced-brace inner text of ``\\title{...}`` in the source (``tex_content`` wins over ``tex_path``); empty string on missing/unreadable input."""
    if tex_content is not None:
        tex = tex_content
    elif tex_path is not None:
        try:
            tex = Path(tex_path).read_text(encoding="utf-8")
        except OSError:
            return ""
    else:
        return ""
    # Find ``\title`` then skip an optional ``[short]`` argument (Beamer's
    # two-arg form ``\title[short]{full}``), then capture the inner of ``{}``.
    marker = r"\title"
    pos = tex.find(marker)
    if pos < 0:
        return ""
    i = pos + len(marker)
    if i < len(tex) and tex[i] == "[":
        # Skip ``[...]`` — assume no nested brackets, which Beamer's short title
        # contract enforces.
        close = tex.find("]", i)
        if close < 0:
            return ""
        i = close + 1
    if i >= len(tex) or tex[i] != "{":
        return ""
    start = i + 1
    depth = 1
    i = start
    while i < len(tex):
        c = tex[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return tex[start:i]
        i += 1
    return ""


def _download_name(
    deck: Any, ext: str, *, title_override: str | None = None
) -> str:
    """Build a human download filename from the deck title (or ``"slides"`` fallback), banned-char-scrubbed and capped at 80 chars.

    Title-resolution order: ``title_override`` → ``plan.talk_title``/``plan.title`` → ``\\title{}`` in ``deck.tex_path`` → ``"slides"``.
    """
    if title_override is not None:
        title = title_override.strip()
    else:
        plan = deck.plan or {}
        title = str(
            plan.get("talk_title") or plan.get("title") or ""
        ).strip()
        if not title and getattr(deck, "tex_path", None):
            title = _read_title_from_tex(str(deck.tex_path)).strip()
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


@router.get("/sessions/{session_id}/deck/slides")
async def get_deck_slides_detail(session_id: int) -> list[dict[str, Any]]:
    """Return one entry per slide of the current deck — the frame source + the
    per-slide source grounding — for the manual frame editor AND the per-page
    Sources strip (F6.2). 404 when the session has no deck.

    Each entry: ``{slide_index, page_start, page_end, frame_tex, source_sections}``
    where ``source_sections`` is the PARSED ``source_sections_json`` (a list of
    ``{paper_id, section_name, chunk_ids}``; malformed → ``[]``).
    """
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
        if deck is None:
            raise HTTPException(status_code=404, detail="no deck for this session")
        rows = await get_deck_slides(conn, deck_id=deck.id)

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            sections = json.loads(r.source_sections_json)
            if not isinstance(sections, list):
                sections = []
        except (ValueError, TypeError):
            sections = []
        out.append({
            "slide_index": r.slide_index,
            "page_start": r.page_start,
            "page_end": r.page_end,
            "frame_tex": r.frame_tex,
            # content_tex = the frame with cite markers stripped — the LaTeX
            # editor shows slide CONTENT only; grounding is managed structurally
            # via the Sources reference editor, never by hand-editing comments.
            "content_tex": strip_cite(r.frame_tex),
            "source_sections": sections,
        })
    return out


@router.get("/sessions/{session_id}/deck/pdf")
async def get_deck_pdf(
    session_id: int, version_id: str | None = None
) -> FileResponse:
    """Stream the compiled PDF; ``?version_id=<v>`` (non-active) serves the cached snapshot PDF, else 404."""
    if version_id is not None and not _VERSION_ID_RE.match(version_id):
        raise HTTPException(status_code=400, detail="invalid version_id")

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None:
        raise HTTPException(
            status_code=404, detail="no compiled PDF for this session"
        )

    if version_id is not None and version_id != (deck.current_version_id or ""):
        edit_history = _slides_workdir(session_id) / "edit_history"
        snapshot = _read_snapshot(edit_history / f"{version_id}.json")
        if snapshot is None:
            raise HTTPException(
                status_code=404, detail=f"snapshot {version_id} not found"
            )
        pdf_filename = snapshot.get("pdf_filename")
        if not isinstance(pdf_filename, str) or not pdf_filename:
            raise HTTPException(
                status_code=404,
                detail=(
                    "this version's PDF is not cached; restore the version to "
                    "recompile, then download"
                ),
            )
        cached_pdf = edit_history / pdf_filename
        if not cached_pdf.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    "this version's PDF is not cached; restore the version to "
                    "recompile, then download"
                ),
            )
        snapshot_title = _read_title_from_tex(
            tex_content=str(snapshot.get("tex_content") or "")
        )
        return FileResponse(
            str(cached_pdf),
            media_type="application/pdf",
            filename=_download_name(deck, "pdf", title_override=snapshot_title),
        )

    if not deck.pdf_path or not _exists(deck.pdf_path):
        raise HTTPException(
            status_code=404, detail="no compiled PDF for this session"
        )
    return FileResponse(
        deck.pdf_path,
        media_type="application/pdf",
        filename=_download_name(deck, "pdf"),
    )


@router.get("/sessions/{session_id}/deck/tex")
async def get_deck_tex(
    session_id: int, version_id: str | None = None
) -> FileResponse:
    """Stream the LaTeX source; ``?version_id=<v>`` (non-active) serves the snapshot's tex_content, else 404."""
    if version_id is not None and not _VERSION_ID_RE.match(version_id):
        raise HTTPException(status_code=400, detail="invalid version_id")

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None:
        raise HTTPException(
            status_code=404, detail="no deck source for this session"
        )

    if version_id is not None and version_id != (deck.current_version_id or ""):
        edit_history = _slides_workdir(session_id) / "edit_history"
        snapshot = _read_snapshot(edit_history / f"{version_id}.json")
        if snapshot is None:
            raise HTTPException(
                status_code=404, detail=f"snapshot {version_id} not found"
            )
        tex_content = str(snapshot.get("tex_content") or "")
        if not tex_content:
            raise HTTPException(
                status_code=404,
                detail=f"snapshot {version_id} has no tex_content",
            )
        # Cache the snapshot tex alongside its pdf so FileResponse streams a
        # real file (no tempfile leak); idempotent write — concurrent
        # downloads race-but-converge on identical bytes.
        cached_tex = edit_history / f"{version_id}.tex"
        if not cached_tex.exists():
            cached_tex.write_text(tex_content, encoding="utf-8")
        snapshot_title = _read_title_from_tex(tex_content=tex_content)
        return FileResponse(
            str(cached_tex),
            media_type="text/plain",
            filename=_download_name(deck, "tex", title_override=snapshot_title),
        )

    if not _exists(deck.tex_path):
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


# ── F6.2 manual LaTeX editing ────────────────────────────────────────────
# Two power-user editors (Slides panel): "edit current frame" splices one
# frame back into deck.tex; "edit all deck" replaces the whole source. Both
# recompile the WHOLE deck mechanically (the user's LaTeX verbatim — NO LLM,
# NO figure audit) and share ``_manual_recompile_and_persist``. The candidate
# compiles under a SCRATCH tex-name so a broken edit never clobbers the
# last-good deck.tex/deck.pdf; a compile failure returns ``ok=false`` + the
# pdflatex log (HTTP 200 — a compile error is a normal editor outcome).

_CANDIDATE_STEM = "deck_candidate"


async def _identity_revise(_log: str, tex: str) -> str:
    """No-op revise: a manual edit is applied verbatim (no LLM cleanup)."""
    return tex


def _cleanup_candidate(slides_dir: Path) -> None:
    """Remove the scratch compile artifacts (best-effort)."""
    for ext in ("tex", "pdf", "aux", "log", "out", "nav", "snm", "toc"):
        with contextlib.suppress(OSError):
            (slides_dir / f"{_CANDIDATE_STEM}.{ext}").unlink()


async def _manual_recompile_and_persist(
    conn: aiosqlite.Connection,
    *,
    session_id: int,
    deck: Any,
    candidate_tex: str,
    description: str,
    preserved_notes: dict[str, str],
    preserved_grounding: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compile ``candidate_tex`` under a scratch tex-name; on success promote it
    to deck.tex/deck.pdf, snapshot a version, upsert the deck, rebuild
    deck_slides, and reapply ``preserved_notes`` by slide_index. Returns the
    JSON body the endpoint sends back.

    Grounding: a CONTENT edit (a single frame, ``preserved_grounding`` given)
    carries each slide's existing ``source_sections`` forward by slide_index —
    editing slide CONTENT must not change its source (that is managed
    structurally via the Sources editor). A whole-deck edit
    (``preserved_grounding=None``) re-derives grounding from the raw ``% cite:``
    markers the user edited directly (``with_grounding``).

    On compile failure the last-good deck.tex/deck.pdf are untouched (they are
    never written unless the candidate compiled) and ``{ok:false, ...}`` is
    returned with the pdflatex log tail.
    """
    slides_dir = _slides_workdir(session_id)
    result = await compile_mod.compile_with_revise(
        tex=candidate_tex,
        workdir=slides_dir,
        tex_name=f"{_CANDIDATE_STEM}.tex",
        revise=_identity_revise,
        max_retries=1,
    )
    if not result.ok:
        await asyncio.to_thread(_cleanup_candidate, slides_dir)
        return {"ok": False, "status": "error", "log": result.log[-4000:]}

    deck_path = slides_dir / "deck.tex"
    pdf_path = slides_dir / "deck.pdf"
    candidate_pdf = slides_dir / f"{_CANDIDATE_STEM}.pdf"

    def _promote() -> str | None:
        deck_path.write_text(result.tex, encoding="utf-8")
        if candidate_pdf.exists():
            shutil.copy2(candidate_pdf, pdf_path)
        version_id = VersionHistory(str(slides_dir)).save_version(
            result.tex, description, preserved_notes
        )
        _cleanup_candidate(slides_dir)
        return version_id

    new_version_id = await asyncio.to_thread(_promote)

    pdf_ok = await asyncio.to_thread(pdf_path.exists)
    await upsert_deck(
        conn,
        session_id=session_id,
        # run_id=None is deliberate: a manual edit is NOT a chat turn, so it
        # stamps no `runs.deck_version_id` and gets no per-turn DeckChip on
        # replay (version history still records it via the snapshot below).
        run_id=None,
        tex_path=str(deck_path),
        pdf_path=str(pdf_path) if pdf_ok else None,
        speaker_notes=deck.speaker_notes,
        plan=deck.plan,
        page_count=result.page_count,
        contributing_paper_ids=deck.contributing_paper_ids,
        status="ok",
        current_version_id=new_version_id or deck.current_version_id,
    )

    fresh = await get_deck(conn, session_id=session_id)
    if fresh is not None:
        built = build_deck_slides(result.tex, result.page_count)
        if preserved_grounding is None:
            inputs = await with_grounding(built, result.tex, conn)
        else:
            # Content edit: carry each slide's existing grounding forward by
            # slide_index (default "[]" for a slide that had none).
            inputs = [
                dataclasses.replace(
                    s,
                    source_sections_json=preserved_grounding.get(
                        str(s.slide_index), "[]"
                    ),
                )
                for s in built
            ]
        await replace_deck_slides(conn, deck_id=fresh.id, slides=inputs)
        if preserved_notes:
            for r in await get_deck_slides(conn, deck_id=fresh.id):
                nt = preserved_notes.get(str(r.slide_index))
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
    return {"ok": True, "status": "ok", "page_count": result.page_count}


def _current_notes_by_index(rows: list[Any]) -> dict[str, str]:
    """Map ``{str(slide_index): note_text}`` for the rows that have a note —
    the notes carried across a manual recompile (rebuild_deck_slides wipes
    them, so they are reapplied by slide_index afterwards)."""
    return {str(r.slide_index): r.note_text for r in rows if r.note_text}


def _current_grounding_by_index(rows: list[Any]) -> dict[str, str]:
    """Map ``{str(slide_index): source_sections_json}`` — the grounding carried
    forward across a CONTENT recompile (editing slide content must not change
    its source; that is managed via the Sources editor)."""
    return {str(r.slide_index): r.source_sections_json for r in rows}


@router.put("/sessions/{session_id}/deck/slides/{page}/tex")
async def edit_deck_frame(
    session_id: int, page: int, body: FrameEdit
) -> dict[str, Any]:
    """Replace the frame occupying ``page`` with the user's edited LaTeX and
    recompile the whole deck. 404 if no deck / no slide covers the page; a
    compile failure returns ``{ok:false, status:"error", log}`` (HTTP 200).
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
        deck_tex = await asyncio.to_thread(
            Path(deck.tex_path).read_text, encoding="utf-8"
        )
        try:
            # The editor sends CONTENT only (no cite marker). Splice it in for
            # the frame body; grounding is carried forward by slide_index (a
            # content edit doesn't change the slide's source — that's the
            # Sources editor's job), so no marker handling here.
            candidate = splice_frame(deck_tex, target.frame_tex, body.frame_tex)
        except ValueError as exc:
            # Absent / ambiguous frame — surface as a 409 so the editor can tell
            # the user to use "Edit all deck" instead of guessing.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await _manual_recompile_and_persist(
            conn,
            session_id=session_id,
            deck=deck,
            candidate_tex=candidate,
            description="manual frame edit",
            preserved_notes=_current_notes_by_index(rows),
            preserved_grounding=_current_grounding_by_index(rows),
        )


@router.put("/sessions/{session_id}/deck/tex")
async def edit_deck_tex(session_id: int, body: DeckEdit) -> dict[str, Any]:
    """Replace the entire deck source with the user's edited LaTeX and
    recompile. 404 if no deck; a compile failure returns
    ``{ok:false, status:"error", log}`` (HTTP 200)."""
    if not body.tex.strip():
        raise HTTPException(status_code=400, detail="empty deck source")
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
        if deck is None:
            raise HTTPException(status_code=404, detail="no deck for this session")
        rows = await get_deck_slides(conn, deck_id=deck.id)
        return await _manual_recompile_and_persist(
            conn,
            session_id=session_id,
            deck=deck,
            candidate_tex=body.tex,
            description="manual deck edit",
            preserved_notes=_current_notes_by_index(rows),
        )


@router.put("/sessions/{session_id}/deck/slides/{page}/sources")
async def edit_deck_slide_sources(
    session_id: int, page: int, body: SlideSourcesEdit
) -> dict[str, Any]:
    """Set the slide's grounding from the structured Sources editor — a
    DETERMINISTIC, comment-only change (no recompile): resolve each
    ``(paper_id, section_name)`` to its chunks, persist ``source_sections`` for
    the slide, and rewrite the frame's ``% cite:`` marker in deck.tex so the DB
    and source stay in sync. Returns ``{ok, source_sections}``.

    404 if there is no deck / no slide covering the page.
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

        # Guard: a slide may only be grounded to a paper attached to THIS
        # session (the same set the Add-source picker offers). Blocks grounding
        # to an arbitrary/off-session paper id from a hand-rolled request.
        async with conn.execute(
            "SELECT paper_content_id FROM papers WHERE session_id = ?",
            (session_id,),
        ) as cur:
            session_papers = {int(r[0]) for r in await cur.fetchall()}
        off_session = sorted(
            {s.paper_id for s in body.sources if s.paper_id not in session_papers}
        )
        if off_session:
            raise HTTPException(
                status_code=400,
                detail=f"paper(s) {off_session} are not in this session",
            )

        # Resolve each (paper, section) to its chunk ids (deterministic — the
        # section comes from the paper's real list, so it grounds to real chunks).
        resolved: list[SourceSection] = []
        for src in body.sources:
            res = await read_section_chunks(
                paper_content_id=src.paper_id,
                section_name=src.section_name,
                conn=conn,
            )
            resolved.append(
                SourceSection(
                    paper_id=src.paper_id,
                    section_name=src.section_name,
                    chunk_ids=list(res.chunk_ids),
                )
            )
        # Guard: never persist a source that resolves to NO chunks — that is the
        # exact "claims a section but grounds to nothing" record we must avoid.
        hollow = [
            f"{ss.paper_id}:{ss.section_name}" for ss in resolved if not ss.chunk_ids
        ]
        if hollow:
            raise HTTPException(
                status_code=400,
                detail=f"no chunks resolved for {', '.join(hollow)}",
            )
        sections_json = json.dumps([ss.model_dump() for ss in resolved])

        # Rewrite the frame's marker in deck.tex (best-effort — keeps the source
        # consistent for a later whole-deck edit / regenerate); update the DB
        # row (authoritative for the Sources strip). NO recompile: % cite: is a
        # LaTeX comment, invisible to the compiled PDF.
        marker = serialize_cite(
            [(s.paper_id, s.section_name) for s in resolved]
        )
        new_frame_tex = target.frame_tex
        if _exists(deck.tex_path):
            try:
                deck_tex = await asyncio.to_thread(
                    Path(deck.tex_path).read_text, encoding="utf-8"
                )
                new_deck_tex, new_frame_tex = set_frame_cite_marker(
                    deck_tex, target.frame_tex, marker
                )
                await asyncio.to_thread(
                    Path(deck.tex_path).write_text, new_deck_tex, encoding="utf-8"
                )
            except (OSError, ValueError):
                new_frame_tex = target.frame_tex  # DB still updated below

        await update_slide_grounding(
            conn,
            deck_id=deck.id,
            slide_index=target.slide_index,
            frame_tex=new_frame_tex,
            source_sections_json=sections_json,
        )
    return {"ok": True, "source_sections": [ss.model_dump() for ss in resolved]}


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

        def _restore() -> tuple[bool, bool]:
            if not snapshot_path.exists():
                return False, False
            tex_path = slides_dir / "deck.tex"
            tex_path.parent.mkdir(parents=True, exist_ok=True)
            return VersionHistory(slides_dir).restore_version(
                f"{version_id}.json", str(tex_path)
            )

        ok, pdf_from_cache = await asyncio.to_thread(_restore)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"snapshot {version_id} not found or unreadable",
            )

        pdf_path = slides_dir / "deck.pdf"
        restored_tex = await asyncio.to_thread(
            (slides_dir / "deck.tex").read_text, encoding="utf-8"
        )
        if pdf_from_cache:
            # Hot path (F4.5 Task 16.2): the cached PDF is already on disk —
            # skip the ~4-6s pdflatex roundtrip. Page count comes from the
            # cached file (best-effort: 0 on parse failure, same contract as
            # compile._page_count).
            page_count = await asyncio.to_thread(
                compile_mod._page_count, pdf_path
            )
            compile_ok = True
            result_tex = restored_tex
            result_page_count = page_count
        else:
            # Legacy snapshot (no pdf_filename): recompile so deck.pdf reflects
            # the restored tex. The snapshot was a successful compile when it
            # was saved, so no LLM-revise is needed — we pass an identity
            # revise callback and 1 retry (first run resolves
            # \tableofcontents-style refs; the second produces a final,
            # ref-stable PDF).
            async def _identity_revise(_log: str, tex: str) -> str:
                return tex

            result = await compile_mod.compile_with_revise(
                tex=restored_tex,
                workdir=slides_dir,
                tex_name="deck.tex",
                revise=_identity_revise,
                max_retries=1,
            )
            compile_ok = result.ok
            result_tex = result.tex
            result_page_count = result.page_count

        # Bump current_version_id + page_count + status + pdf_path + updated_at
        # in one statement so a concurrent GET /deck sees the new state
        # atomically. A failed recompile is unexpected (the snapshot used to
        # build cleanly) but we still flip current_version_id so the chip on
        # screen matches the source — status=error surfaces the failure.
        new_pdf_path = str(pdf_path) if compile_ok and pdf_path.exists() else None
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
                result_page_count if compile_ok else 0,
                new_pdf_path,
                "ok" if compile_ok else "error",
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
        if fresh is not None and compile_ok:
            await replace_deck_slides(
                conn,
                deck_id=fresh.id,
                slides=await with_grounding(
                    build_deck_slides(result_tex, result_page_count),
                    result_tex,
                    conn,
                ),
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
        "page_count": result_page_count if compile_ok else 0,
        "status": "ok" if compile_ok else "error",
        "cache_hit": pdf_from_cache,
    }


__all__ = ["router"]
