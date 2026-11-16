"""Notes REST surface (Plan H5b.1): a note per paper / chat / idea, edited by
block ops. Conflicts of an edit are reported in ``rejected`` (never a 409);
only a second Apply again of one change set is a 409."""
from __future__ import annotations

import json
from typing import Any, Literal

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.notes import comments as note_comments
from paperhub.notes import ops as note_ops
from paperhub.notes.models import Block, Note
from paperhub.notes.ops import ApplyResult, Op
from paperhub.notes.papers import paper_note
from paperhub.notes.store import (
    get_note,
    get_or_create_note,
    list_blocks,
    shared_by,
)

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteWithBlocks(BaseModel):
    note: Note
    blocks: list[Block]
    # How many live chats the note is linked to ("Shared by N chats").
    shared_by: int


class NoteDetail(NoteWithBlocks):
    suggestions: list[dict[str, Any]]
    comments: list[dict[str, Any]]


class OpsBody(BaseModel):
    base_revision: int
    ops: list[Op]
    # One burst across its retries; a repeated key returns the first result.
    idempotency_key: str | None = None


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
        suggestions=suggestions,
        comments=await note_comments.list_comments(conn, note.id))


@router.get("/target", response_model=NoteDetail)
async def note_for_target(
    kind: Literal["paper", "session", "idea"], id: int = Query(),
) -> NoteDetail:
    """The note of a paper / chat / idea, created on first use, as
    ``GET /notes/{id}`` shows it (open suggestions and comments too)."""
    async with open_db(load_settings().db_path) as conn:
        if kind == "paper":
            try:
                note = await paper_note(conn, id)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
        else:
            title = await _title_for(conn, kind, id)
            note = await get_or_create_note(
                conn, target_kind=kind, target_id=str(id), title=title,
                session_id=id if kind == "session" else None)
        return await _detail(conn, note)


class BlockPlace(BaseModel):
    note_id: int
    target_kind: str
    target_id: str


@router.get("/blocks/{block_id}", response_model=BlockPlace)
async def block_place(block_id: str) -> BlockPlace:
    """The note a live block is in, and that note's target (a ``[note:B]``
    chip in the chat opens the notes pane there)."""
    async with open_db(load_settings().db_path) as conn:
        async with conn.execute(
            "SELECT n.id, n.target_kind, n.target_id FROM note_blocks b "
            "JOIN notes n ON n.id = b.note_id "
            "WHERE b.id = ? AND b.deleted_at IS NULL AND n.deleted_at IS NULL",
            (block_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"block {block_id} not found")
    return BlockPlace(note_id=row[0], target_kind=row[1], target_id=str(row[2]))


@router.get("/{note_id}", response_model=NoteDetail)
async def note_detail(note_id: int) -> NoteDetail:
    async with open_db(load_settings().db_path) as conn:
        note = await get_note(conn, note_id)
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
