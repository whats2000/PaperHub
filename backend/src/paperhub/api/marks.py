"""Reading-marks REST surface (Plan H5b.1): highlights, quotes, figures,
equations and regions on a paper, each backed by a block in its paper note."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.notes import marks as marks_store
from paperhub.notes.marks import (
    CiteAt,
    MarkConflict,
    MarkIn,
    MarkInputError,
    SavedTo,
    block_props,
)
from paperhub.notes.ops import ApplyResult
from paperhub.notes.papers import paper_note
from paperhub.notes.store import list_blocks

router = APIRouter(prefix="/marks", tags=["marks"])


class MarkCreated(BaseModel):
    mark: dict[str, Any]
    block: dict[str, Any] | None
    saved_to: SavedTo
    # A short mark landed as an inline citation: its block, the offset
    # just after the chip, and whether that paragraph was made for it.
    cite: dict[str, Any] | None = None


class RestoreIn(BaseModel):
    cite_at: CiteAt | None = None


def _out(m: dict[str, Any]) -> dict[str, Any]:
    """A mark as the client gets it: with its reference block's props."""
    return {**m, "block_props": block_props(m)}


class ColorPatch(BaseModel):
    name: str | None = None
    visible: bool | None = None
    hex: str | None = None


class ColorNew(BaseModel):
    hex: str
    name: str


class ColorOrder(BaseModel):
    order: list[str]


@asynccontextmanager
async def _db() -> AsyncIterator[aiosqlite.Connection]:
    """A connection whose store errors map to HTTP statuses: unknown id 404,
    invalid input 422, a mark not in the state the request expects (a
    rejected block op, a mark deleted or changed elsewhere) 409; anything
    else is a 500."""
    async with open_db(load_settings().db_path) as conn:
        try:
            yield conn
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except MarkInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        except MarkConflict as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("", response_model=MarkCreated)
async def create_mark(body: MarkIn) -> MarkCreated:
    async with _db() as conn:
        mark, saved = await marks_store.create_mark(conn, body)
        cite = mark.pop("cite", None)
        note = await paper_note(conn, mark["paper_content_id"])
        block = next((b for b in await list_blocks(conn, note.id)
                      if b.id == mark["block_id"]), None)
        mark["cite_block_id"] = cite["block_id"] if cite else None
        return MarkCreated(
            mark=_out(mark), block=block.model_dump() if block else None,
            saved_to=saved, cite=cite)


@router.get("/colors")
async def list_colors() -> list[dict[str, Any]]:
    async with _db() as conn:
        return await marks_store.colors(conn)


@router.patch("/colors/{color}")
async def patch_color(color: str, body: ColorPatch) -> list[dict[str, Any]]:
    async with _db() as conn:
        await marks_store.set_color(conn, color, name=body.name,
                                    visible=body.visible, hex=body.hex)
        return await marks_store.colors(conn)


@router.post("/colors")
async def add_color(body: ColorNew) -> list[dict[str, Any]]:
    """A new labelled colour (422 with the reason when refused)."""
    async with _db() as conn:
        await marks_store.add_color(conn, hex=body.hex, name=body.name)
        return await marks_store.colors(conn)


@router.put("/colors/order")
async def reorder_colors(body: ColorOrder) -> list[dict[str, Any]]:
    async with _db() as conn:
        await marks_store.reorder_colors(conn, body.order)
        return await marks_store.colors(conn)


@router.delete("/colors/{color}")
async def delete_color(color: str) -> list[dict[str, Any]]:
    async with _db() as conn:
        await marks_store.delete_color(conn, color)
        return await marks_store.colors(conn)


@router.get("")
async def list_marks(paper_content_id: int) -> list[dict[str, Any]]:
    async with _db() as conn:
        return await marks_store.list_marks(conn, paper_content_id)


@router.delete("/{mark_id}", response_model=ApplyResult)
async def delete_mark(mark_id: str, remove_cite: bool = False,
                      drop_block: str | None = None) -> ApplyResult:
    """``remove_cite`` (Undo of a short mark's creation) also takes its
    inline citation out; ``drop_block`` the paragraph made for it."""
    async with _db() as conn:
        return await marks_store.delete_mark(
            conn, mark_id, remove_cite=remove_cite, drop_block=drop_block)


@router.post("/{mark_id}/restore", response_model=ApplyResult)
async def restore_mark(mark_id: str, body: RestoreIn | None = None,
                       ) -> ApplyResult:
    """``cite_at`` (Redo of a short mark's creation) puts its inline
    citation back there."""
    async with _db() as conn:
        return await marks_store.restore_mark(
            conn, mark_id, body.cite_at if body else None)


@router.delete("/{mark_id}/pieces/{index}")
async def erase_piece(mark_id: str, index: int) -> dict[str, Any]:
    async with _db() as conn:
        return _out(await marks_store.erase_piece(conn, mark_id, index))


@router.post("/{mark_id}/pieces/{index}/restore")
async def restore_piece(mark_id: str, index: int) -> dict[str, Any]:
    async with _db() as conn:
        return _out(await marks_store.restore_piece(conn, mark_id, index))


@router.post("/{mark_id}/split")
async def split_mark(mark_id: str) -> dict[str, Any]:
    """The new mark, with ``original``: the mark it came from as it now is
    (its moved piece flagged ``removed``)."""
    async with _db() as conn:
        new = await marks_store.split_last_piece(conn, mark_id)
        return {**_out(new),
                "original": _out(await marks_store.get_mark(conn, mark_id))}
