"""Notes REST surface (Plan H5b.1): a note per paper / chat / idea, edited by
block ops. Conflicts of an edit are reported in ``rejected`` (never a 409);
only a second Apply again of one change set is a 409."""
from __future__ import annotations

import asyncio
import functools
import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

import aiosqlite
from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from paperhub.config import load_settings
from paperhub.db.connection import in_write_transaction, open_db, write_lock
from paperhub.notes import comments as note_comments
from paperhub.notes import ops as note_ops
from paperhub.notes import pandoc as note_pandoc
from paperhub.notes.export import (
    display_name,
    export_sources,
    note_to_markdown,
    slug,
)
from paperhub.notes.models import Block, Note
from paperhub.notes.ops import ApplyResult, Op
from paperhub.notes.papers import paper_note
from paperhub.notes.store import (
    default_title,
    find_note,
    get_note,
    get_or_create_note,
    list_blocks,
    list_live_notes,
    named,
    set_title,
    shared_by,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteWithBlocks(BaseModel):
    note: Note
    blocks: list[Block]
    # How many live chats the note is linked to ("Shared by N chats").
    shared_by: int
    # The page title shown when ``note.title`` is empty: the target's
    # current title ("" when that is empty too; the client says "Untitled").
    default_title: str = ""


class NoteDetail(NoteWithBlocks):
    suggestions: list[dict[str, Any]]
    comments: list[dict[str, Any]]


class OpsBody(BaseModel):
    base_revision: int
    ops: list[Op]
    # One burst across its retries; a repeated key returns the first result.
    idempotency_key: str | None = None


class TitleBody(BaseModel):
    title: str = Field(max_length=500)


class RestoreBody(BaseModel):
    block_ids: list[str]


class SuggestionBody(BaseModel):
    accept: bool


class CommentBody(BaseModel):
    block_id: str
    quote: str
    text: str


class CommentItemBody(BaseModel):
    text: str


class CommentStatusBody(BaseModel):
    status: Literal["open", "resolved", "deleted"]


async def _title_for(conn: aiosqlite.Connection, kind: str, target: int) -> str:
    sql = {
        "session": "SELECT title FROM chat_sessions WHERE id = ? AND internal = 0",
        "idea": "SELECT title FROM ideas WHERE id = ?",
    }[kind]
    async with conn.execute(sql, (target,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"{kind} {target} not found")
    return str(row[0])


async def _detail(conn: aiosqlite.Connection, note: Note) -> NoteDetail:
    """A note with its blocks, open suggestions and comment threads."""
    async with conn.execute(
        "SELECT id, block_id, change_set_id, kind, proposed_json, status, "
        "created_at FROM note_suggestions WHERE note_id = ? "
        "AND status = 'open' ORDER BY created_at, rowid", (note.id,),
    ) as cur:
        rows = await cur.fetchall()
    suggestions = [
        {"id": r[0], "block_id": r[1], "change_set_id": r[2], "kind": r[3],
         "proposed": json.loads(r[4]) if r[4] else None, "status": r[5],
         "created_at": r[6]} for r in rows]
    return NoteDetail(
        note=note, blocks=await list_blocks(conn, note.id),
        shared_by=await shared_by(conn, note),
        default_title=await default_title(conn, note),
        suggestions=suggestions,
        comments=await note_comments.list_comments(conn, note.id))


class NoNoteYet(BaseModel):
    """A target with no note yet: nothing is created by looking (the first
    write creates it, ``POST /notes/target``)."""
    note: None = None
    blocks: list[Block] = []
    shared_by: int
    default_title: str = ""
    suggestions: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []


async def _check_target(conn: aiosqlite.Connection, kind: str, target: int) -> None:
    """404 for a missing paper / chat / idea."""
    if kind == "paper":
        async with conn.execute(
            "SELECT 1 FROM paper_content WHERE id = ?", (target,)
        ) as cur:
            if await cur.fetchone() is None:
                raise HTTPException(404, f"paper_content {target} not found")
    else:
        await _title_for(conn, kind, target)


@router.get("/target", response_model=NoteDetail | NoNoteYet)
async def note_for_target(
    kind: Literal["paper", "session", "idea"], id: int = Query(),
) -> NoteDetail | NoNoteYet:
    """The note of a paper / chat / idea as ``GET /notes/{id}`` shows it
    (open suggestions and comments too), or ``note: null`` when it has
    none yet. Never creates one."""
    async with open_db(load_settings().db_path) as conn:
        await _check_target(conn, kind, id)
        note = await find_note(conn, target_kind=kind, target_id=str(id))
        if note is not None:
            return await _detail(conn, note)
        # What the note would show: the target's title and chats.
        probe = Note(id=0, target_kind=kind, target_id=str(id), title="",
                     revision=0, updated_at="", deleted_at=None)
        return NoNoteYet(shared_by=await shared_by(conn, probe),
                         default_title=await default_title(conn, probe))


@router.post("/target", response_model=NoteDetail)
async def create_note_for_target(
    kind: Literal["paper", "session", "idea"], id: int = Query(),
) -> NoteDetail:
    """The target's note, created now if it has none (the first write of
    the notes pane / the note card). Idempotent: a second call returns the
    same note."""
    async with open_db(load_settings().db_path) as conn:
        await _check_target(conn, kind, id)
        if kind == "paper":
            note = await paper_note(conn, id)
        else:
            note = await get_or_create_note(
                conn, target_kind=kind, target_id=str(id), title="",
                session_id=id if kind == "session" else None)
        return await _detail(conn, note)


_DOCX_TYPE = ("application/vnd.openxmlformats-officedocument."
              "wordprocessingml.document")


@router.get("/export/capabilities")
async def export_capabilities() -> dict[str, Any]:
    """Which export formats work here: Word needs pandoc 2.15+ (its
    ``--sandbox``); ``docx_reason`` says why it is unavailable."""
    status = note_pandoc.pandoc_status()
    return {"md": True, "docx": status == "ok",
            "docx_reason": None if status == "ok" else status}


def _zip(md: str, images: dict[str, bytes], bib: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("note.md", md)
        for name, data in images.items():
            z.writestr(name, data)
        z.writestr("references.bib", bib)
    return buf.getvalue()


async def _record_export(conn: aiosqlite.Connection, note_id: int, fmt: str,
                         actor: str = "user") -> None:
    async with write_lock(conn):
        await conn.execute(
            "INSERT INTO note_exports (actor, note_id, format, created_at) "
            "VALUES (?, ?, ?, ?)",
            (actor, note_id, fmt, datetime.now(UTC).isoformat()))
        if not in_write_transaction():
            await conn.commit()


@router.get("/{note_id}/export")
async def export_note(note_id: int,
                      format: Literal["md", "docx"] = Query()) -> Response:
    """The note as a Markdown zip (``note.md``, ``images/``,
    ``references.bib``) or a ``.docx``; 409 ``pandoc_missing`` for Word
    without pandoc. Each export is recorded in ``note_exports``."""
    if format == "docx":
        status = note_pandoc.pandoc_status()
        if status != "ok":
            raise HTTPException(409, status)
    async with open_db(load_settings().db_path) as conn:
        note = await get_note(conn, note_id)
        if note is None:
            raise HTTPException(404, f"note {note_id} not found")
        blocks = await list_blocks(conn, note_id)
        try:
            # Never an empty heading or file name: title, else default.
            note = await named(conn, note)
            papers, chunk_papers, mark_papers, mark_cites = await export_sources(
                conn, note, blocks)
            md, images, bib = await asyncio.to_thread(functools.partial(
                note_to_markdown, note, blocks, papers,
                chunk_papers=chunk_papers, mark_papers=mark_papers,
                mark_cites=mark_cites))
            name = slug(note.title) + "-notes"
            full_name = display_name(note.title) + "-notes"
            if format == "md":
                body = _zip(md, images, bib)
                media, ext = "application/zip", "zip"
            else:
                body = await asyncio.to_thread(
                    note_pandoc.markdown_to_docx, md,
                    {**images, note_pandoc.BIB_NAME: bib.encode("utf-8")})
                media, ext = _DOCX_TYPE, "docx"
        except note_pandoc.PandocError as exc:
            logger.warning("note %s: Word export failed: %s", note_id, exc)
            raise HTTPException(500, "pandoc_failed") from exc
        except Exception as exc:
            # Any other failure is a clean 500 with the cause in the log,
            # never a traceback to the client.
            logger.exception("note %s: %s export failed", note_id, format)
            raise HTTPException(500, "export_failed") from exc
        await _record_export(conn, note_id, format)
    logger.info("note export: note=%s format=%s blocks=%d images=%d",
                note_id, format, len(blocks), len(images))
    return Response(body, media_type=media, headers={
        # An ASCII name for old clients, the title as typed for the rest.
        "Content-Disposition": (
            f'attachment; filename="{name}.{ext}"; '
            f"filename*=UTF-8''{quote(f'{full_name}.{ext}', safe='')}")})


class BlockPlace(BaseModel):
    note_id: int
    target_kind: str
    target_id: str


@router.get("/blocks/{block_id}", response_model=BlockPlace)
async def block_place(block_id: str = Path(max_length=64)) -> BlockPlace:
    """The note a live block is in, and that note's target (a ``[note:B]``
    chip in the chat opens the notes pane there)."""
    async with open_db(load_settings().db_path) as conn, conn.execute(
        "SELECT n.id, n.target_kind, n.target_id FROM note_blocks b "
        "JOIN notes n ON n.id = b.note_id "
        "WHERE b.id = ? AND b.deleted_at IS NULL AND n.deleted_at IS NULL",
        (block_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"block {block_id} not found")
    return BlockPlace(note_id=row[0], target_kind=row[1], target_id=str(row[2]))


class NoteHit(BaseModel):
    note_id: int
    kind: str
    target_id: int
    title: str


class HeadingHit(BaseModel):
    note_id: int
    note_title: str
    block_id: str
    text: str
    level: int


class OutlineItem(BaseModel):
    block_id: str
    type: Literal["heading", "section"]
    text: str
    level: int


def _level(props: dict[str, Any]) -> int:
    lv = props.get("level")
    return lv if isinstance(lv, int) and not isinstance(lv, bool) and lv > 0 else 1


@router.get("/search", response_model=list[NoteHit])
async def search_notes(q: str = "", limit: int = Query(20, ge=1, le=100)
                       ) -> list[NoteHit]:
    """Live notes whose display title contains ``q`` (a ``[[`` link's note
    target picker)."""
    async with open_db(load_settings().db_path) as conn:
        hits = await list_live_notes(conn, q, limit=limit)
    return [NoteHit(note_id=n.id, kind=n.target_kind,
                    target_id=int(n.target_id) if n.target_id.isdigit() else 0,
                    title=title) for n, title in hits]


@router.get("/headings/search", response_model=list[HeadingHit])
async def search_headings(q: str = "", limit: int = Query(20, ge=1, le=100)
                          ) -> list[HeadingHit]:
    """Live headings whose text contains ``q``, in live notes (a ``[[``
    link to a heading)."""
    needle = q.strip().casefold()
    out: list[HeadingHit] = []
    async with open_db(load_settings().db_path) as conn:
        for note, note_title in await list_live_notes(conn):
            for b in await list_blocks(conn, note.id):
                if b.type != "heading" or needle not in b.text.casefold():
                    continue
                out.append(HeadingHit(
                    note_id=note.id, note_title=note_title, block_id=b.id,
                    text=b.text, level=_level(b.props)))
                if len(out) >= limit:
                    return out
    return out


@router.get("/{note_id}/outline", response_model=list[OutlineItem])
async def note_outline(note_id: int) -> list[OutlineItem]:
    """A live note's headings and paper sections in document order
    (children of a toggle heading included): the in-note TOC and the
    heading picker of a ``[[`` link."""
    async with open_db(load_settings().db_path) as conn:
        note = await get_note(conn, note_id)
        async with conn.execute(
            "SELECT 1 FROM notes WHERE id = ? AND owner_id = 1", (note_id,)
        ) as cur:
            owned = await cur.fetchone() is not None
        if note is None or note.deleted_at is not None or not owned:
            raise HTTPException(404, f"note {note_id} not found")
        out: list[OutlineItem] = []
        for b in await list_blocks(conn, note_id):
            # An unnamed entry is no link target: left out, as in the
            # in-note table of contents and its export.
            if b.type == "heading" and b.text.strip():
                out.append(OutlineItem(block_id=b.id, type="heading",
                                       text=b.text, level=_level(b.props)))
            elif b.type == "section":
                name = (b.text or b.section_key or "").strip()
                if name:
                    out.append(OutlineItem(block_id=b.id, type="section",
                                           text=name, level=1))
        return out


@router.get("/{note_id}", response_model=NoteDetail)
async def note_detail(note_id: int) -> NoteDetail:
    async with open_db(load_settings().db_path) as conn:
        note = await get_note(conn, note_id)
        if note is None:
            raise HTTPException(404, f"note {note_id} not found")
        return await _detail(conn, note)


@router.patch("/{note_id}", response_model=NoteDetail)
async def rename_note(note_id: int, body: TitleBody) -> NoteDetail:
    """Set the note's page title ("" falls back to ``default_title``)."""
    async with open_db(load_settings().db_path) as conn:
        note = await set_title(conn, note_id, body.title)
        if note is None:
            raise HTTPException(404, f"note {note_id} not found")
        return await _detail(conn, note)


@router.post("/{note_id}/ops", response_model=ApplyResult)
async def apply_note_ops(note_id: int, body: OpsBody) -> ApplyResult:
    async with open_db(load_settings().db_path) as conn:
        if await get_note(conn, note_id) is None:
            raise HTTPException(404, f"note {note_id} not found")
        try:
            return await note_ops.apply_ops(
                conn, note_id, base_revision=body.base_revision, ops=body.ops,
                actor="user", idempotency_key=body.idempotency_key)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post("/{note_id}/restore", response_model=ApplyResult)
async def restore_note_blocks(note_id: int, body: RestoreBody) -> ApplyResult:
    async with open_db(load_settings().db_path) as conn:
        if await get_note(conn, note_id) is None:
            raise HTTPException(404, f"note {note_id} not found")
        try:
            return await note_ops.restore_blocks(
                conn, note_id, body.block_ids, actor="user")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post("/suggestions/{suggestion_id}", response_model=ApplyResult)
async def decide_suggestion(suggestion_id: str,
                            body: SuggestionBody) -> ApplyResult:
    async with open_db(load_settings().db_path) as conn:
        try:
            return await note_ops.resolve_suggestion(
                conn, suggestion_id, body.accept)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/change-sets/{change_set_id}/undo", response_model=ApplyResult)
async def undo_change_set(change_set_id: str) -> ApplyResult:
    async with open_db(load_settings().db_path) as conn:
        try:
            return await note_ops.undo_change_set(conn, change_set_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/change-sets/{change_set_id}/reapply", response_model=ApplyResult)
async def reapply_change_set(change_set_id: str) -> ApplyResult:
    async with open_db(load_settings().db_path) as conn:
        try:
            return await note_ops.reapply_change_set(conn, change_set_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.get("/change-sets/{change_set_id}")
async def change_set_status(change_set_id: str) -> dict[str, Any]:
    """Where a change set stands (and the set live after Apply again), so a
    card shows Undo or Apply again, and what it changed (its Details)."""
    async with open_db(load_settings().db_path) as conn:
        try:
            return await note_ops.change_set_detail(conn, change_set_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("/{note_id}/comments")
async def create_comment(note_id: int, body: CommentBody) -> dict[str, Any]:
    async with open_db(load_settings().db_path) as conn:
        if await get_note(conn, note_id) is None:
            raise HTTPException(404, f"note {note_id} not found")
        try:
            return await note_comments.create_comment(
                conn, note_id, block_id=body.block_id, quote=body.quote,
                text=body.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post("/comments/{comment_id}/items")
async def reply_to_comment(comment_id: str,
                           body: CommentItemBody) -> dict[str, Any]:
    async with open_db(load_settings().db_path) as conn:
        try:
            return await note_comments.add_item(conn, comment_id, body.text)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except note_comments.CommentConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.patch("/comments/{comment_id}")
async def set_comment_status(comment_id: str,
                             body: CommentStatusBody) -> dict[str, Any]:
    async with open_db(load_settings().db_path) as conn:
        try:
            return await note_comments.set_status(conn, comment_id, body.status)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except note_comments.CommentConflict as exc:
            raise HTTPException(409, str(exc)) from exc
