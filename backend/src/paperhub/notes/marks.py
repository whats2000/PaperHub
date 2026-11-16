"""Reading marks on papers: highlights, quotes, figures, equations, regions.

``note_marks`` is the source of truth; each mark owns one note block whose
props carry what the editor needs to render it. Every block write goes
through the op engine (actor ``user``) so history, revisions and Undo work,
and each public operation is ONE write transaction covering its block ops
and its ``note_marks`` writes: either all of it lands or none does.
"""
from __future__ import annotations

import json
import secrets
import string
from datetime import UTC, datetime
from typing import Any

import aiosqlite
from pydantic import BaseModel

from paperhub.db.connection import in_write_transaction, write_lock
from paperhub.notes.ops import (
    ApplyResult,
    BlockIn,
    Op,
    _apply_in_tx,
    _restore_in_tx,
)
from paperhub.notes.papers import (
    destination,
    ensure_section,
    paper_note,
    parts_of_section,
    tx,
)
from paperhub.notes.store import get_note, list_blocks, new_block_id

_BLOCK_TYPE: dict[str, Any] = {
    "highlight": "paperQuote", "quote": "paperQuote", "figure": "paperFigure",
    "equation": "paperEquation", "region": "paperRegion",
}
_VIEWS = ("html", "pdf")
_COLS = ("id, paper_content_id, block_id, kind, color, view, pieces_json, "
         "lang, source_ref, section_key, status, created_at, deleted_at")
_INSERT = (
    "INSERT INTO note_marks (id, paper_content_id, block_id, kind, color, "
    "view, pieces_json, lang, source_ref, section_key, status, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)")


class MarkInputError(ValueError):
    """The request names something invalid (kind, view, piece, ...)."""


class MarkConflict(RuntimeError):
    """The mark's block could not be changed (e.g. the user edited it)."""


class MarkIn(BaseModel):
    paper_content_id: int
    kind: str
    color: str | None
    view: str
    pieces: list[dict[str, Any]]
    lang: str | None = None
    source_ref: str | None = None
    section_key: str | None
    hint_block_id: str | None = None
    join_to: str | None = None


class SavedTo(BaseModel):
    note_id: int
    paper_title: str
    section: str | None
    part_index: int
    part_count: int


def _mark_id() -> str:
    return "mk_" + "".join(
        secrets.choice(string.ascii_lowercase + string.digits)
        for _ in range(10))


def _mark(row: Any) -> dict[str, Any]:
    return {
        "id": row[0], "paper_content_id": row[1], "block_id": row[2],
        "kind": row[3], "color": row[4], "view": row[5],
        "pieces": json.loads(row[6]), "lang": row[7], "source_ref": row[8],
        "section_key": row[9], "status": row[10], "created_at": row[11],
        "deleted_at": row[12],
    }


async def _get(conn: aiosqlite.Connection, mark_id: str) -> dict[str, Any]:
    async with conn.execute(
        f"SELECT {_COLS} FROM note_marks WHERE id = ?", (mark_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"mark {mark_id} not found")
    return _mark(row)


async def _write(conn: aiosqlite.Connection, sql: str,
                 params: tuple[Any, ...]) -> None:
    async with write_lock(conn):
        await conn.execute(sql, params)
        if not in_write_transaction():
            await conn.commit()


def _live(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pieces still shown: neither erased nor moved to another mark."""
    return [p for p in pieces if not p.get("erased") and not p.get("removed")]


def _check_index(m: dict[str, Any], index: int) -> None:
    if not 0 <= index < len(m["pieces"]):
        raise MarkInputError(f"piece {index} out of range")
    if m["pieces"][index].get("removed"):
        raise MarkInputError(f"piece {index} belongs to another mark now")


def _validate(kind: str, view: str, pieces: list[dict[str, Any]]) -> None:
    if kind not in _BLOCK_TYPE:
        raise MarkInputError(f"unknown mark kind {kind!r}")
    if view not in _VIEWS:
        raise MarkInputError(f"unknown mark view {view!r}")
    if not pieces:
        raise MarkInputError("a mark needs at least one piece")


def _block_props(m: dict[str, Any]) -> dict[str, Any]:
    live = _live(m["pieces"])
    first = live[0] if live else {}
    source: dict[str, Any] = {}
    if first.get("page") is not None:
        source["page"] = first["page"]
    if m["section_key"]:
        source["section"] = m["section_key"]
    props: dict[str, Any] = {
        "markId": m["id"], "color": m["color"], "kind": m["kind"],
        "quote": " ".join(str(p.get("quote") or "") for p in live).strip(),
        "pieces": live, "source": source,
    }
    if m["status"] == "lost":
        props["anchorLost"] = True
    elif m["status"] == "partly_lost":
        props["piecesLost"] = sum(1 for p in live if p.get("lost"))
    if m["lang"]:
        props["lang"] = m["lang"]
    if first.get("orig"):
        props["orig"] = first["orig"]
    return props


async def _note_id(conn: aiosqlite.Connection, m: dict[str, Any]) -> int:
    return (await paper_note(conn, m["paper_content_id"])).id


async def _apply(conn: aiosqlite.Connection, note_id: int, op: Op,
                 summary: str, expect: str) -> ApplyResult:
    """Run one block op inside the caller's transaction; it must land."""
    note = await get_note(conn, note_id)
    assert note is not None
    res = await _apply_in_tx(
        conn, note_id, base_revision=note.revision, ops=[op], actor="user",
        run_id=None, summary=summary)
    if expect not in res.applied:
        raise MarkConflict(
            f"block {expect} was not changed: {res.rejected or 'rejected'}")
    return res


async def _update_block(conn: aiosqlite.Connection, m: dict[str, Any]) -> None:
    """Push the mark's current pieces into its block's props."""
    await _apply(
        conn, await _note_id(conn, m),
        Op(op="update", block_id=m["block_id"],
           block=BlockIn(type=_BLOCK_TYPE[m["kind"]],
                         props=_block_props(m))),
        "Edit mark", m["block_id"])


async def _save_pieces(conn: aiosqlite.Connection, m: dict[str, Any]) -> None:
    await _write(conn, "UPDATE note_marks SET pieces_json = ? WHERE id = ?",
                 (json.dumps(m["pieces"]), m["id"]))


async def _insert_mark(conn: aiosqlite.Connection, note_id: int,
                       rec: dict[str, Any], after: str | None,
                       summary: str) -> None:
    await _apply(
        conn, note_id,
        Op(op="insert", block_id=rec["block_id"], after=after,
           block=BlockIn(type=_BLOCK_TYPE[rec["kind"]],
                         props=_block_props(rec))),
        summary, rec["block_id"])
    await _write(conn, _INSERT, (
        rec["id"], rec["paper_content_id"], rec["block_id"], rec["kind"],
        rec["color"], rec["view"], json.dumps(rec["pieces"]), rec["lang"],
        rec["source_ref"], rec["section_key"], rec["created_at"]))


async def create_mark(conn: aiosqlite.Connection, m: MarkIn,
                      ) -> tuple[dict[str, Any], SavedTo]:
    _validate(m.kind, m.view, m.pieces)
    async with tx(conn):
        if m.join_to is not None:
            return await _join(conn, m)
        note = await paper_note(conn, m.paper_content_id)
        after: str | None = None
        part_index, part_count = 0, 1
        if m.section_key:
            sec = await ensure_section(conn, note.id, m.paper_content_id,
                                       m.section_key)
            after, part_index, part_count = await destination(
                conn, note.id, sec, m.hint_block_id)
        rec: dict[str, Any] = {
            "id": _mark_id(), "paper_content_id": m.paper_content_id,
            "block_id": new_block_id(), "kind": m.kind, "color": m.color,
            "view": m.view, "pieces": m.pieces, "lang": m.lang,
            "source_ref": m.source_ref, "section_key": m.section_key,
            "status": "ok", "created_at": datetime.now(UTC).isoformat(),
            "deleted_at": None,
        }
        await _insert_mark(conn, note.id, rec, after, "Add mark")
        return rec, SavedTo(note_id=note.id, paper_title=note.title,
                            section=m.section_key, part_index=part_index,
                            part_count=part_count)


async def _section_block(conn: aiosqlite.Connection, note_id: int,
                         section_key: str | None) -> str | None:
    if not section_key:
        return None
    for b in await list_blocks(conn, note_id):
        if b.type == "section" and b.section_key == section_key:
            return b.id
    return None


async def _saved_to(conn: aiosqlite.Connection, m: dict[str, Any]) -> SavedTo:
    note = await paper_note(conn, m["paper_content_id"])
    index, count = 0, 1
    sec_id = await _section_block(conn, note.id, m["section_key"])
    if sec_id is not None:
        parts = parts_of_section(await list_blocks(conn, note.id), sec_id)
        count = len(parts)
        index = next((i for i, p in enumerate(parts)
                      if any(b.id == m["block_id"] for b in p)), 0)
    return SavedTo(note_id=note.id, paper_title=note.title,
                   section=m["section_key"], part_index=index,
                   part_count=count)


async def _join(conn: aiosqlite.Connection, m: MarkIn,
                ) -> tuple[dict[str, Any], SavedTo]:
    assert m.join_to is not None
    rec = await _get(conn, m.join_to)
    if rec["deleted_at"] is not None:
        raise MarkInputError("cannot join a deleted mark")
    if (rec["paper_content_id"], rec["color"], rec["kind"], rec["view"]) != (
            m.paper_content_id, m.color, m.kind, m.view):
        raise MarkInputError("a piece can only join a mark of the same paper, "
                         "colour, kind and view")
    rec["pieces"] = [*rec["pieces"], *m.pieces]
    await _save_pieces(conn, rec)
    await _update_block(conn, rec)
    return rec, await _saved_to(conn, rec)


async def list_marks(conn: aiosqlite.Connection,
                     paper_content_id: int) -> list[dict[str, Any]]:
    async with conn.execute(
        f"SELECT {_COLS} FROM note_marks WHERE paper_content_id = ? "
        "AND deleted_at IS NULL ORDER BY created_at, rowid",
        (paper_content_id,),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return []
    note = await paper_note(conn, paper_content_id)
    blocks = await list_blocks(conn, note.id)
    sections = {b.section_key: b.id for b in blocks if b.type == "section"}
    out: list[dict[str, Any]] = []
    for r in rows:
        m = _mark(r)
        label = m["section_key"] or ""
        sec_id = sections.get(m["section_key"])
        if sec_id is not None:
            parts = parts_of_section(blocks, sec_id)
            if len(parts) > 1:
                i = next((i for i, p in enumerate(parts)
                          if any(b.id == m["block_id"] for b in p)), 0)
                label = f"{label} · {i + 1}"
        m["note_id"] = note.id
        m["part_label"] = label
        out.append(m)
    return out


async def _delete(conn: aiosqlite.Connection, m: dict[str, Any],
                  ) -> ApplyResult:
    res = await _apply(conn, await _note_id(conn, m),
                       Op(op="delete", block_id=m["block_id"]),
                       "Delete mark", m["block_id"])
    await _write(conn, "UPDATE note_marks SET deleted_at = ? WHERE id = ?",
                 (datetime.now(UTC).isoformat(), m["id"]))
    m["deleted_at"] = "now"
    return res


async def _restore(conn: aiosqlite.Connection, m: dict[str, Any],
                   ) -> ApplyResult:
    res = await _restore_in_tx(conn, await _note_id(conn, m),
                               [m["block_id"]], actor="user")
    if m["block_id"] not in res.applied:
        raise MarkConflict(
            f"block {m['block_id']} was not restored: "
            f"{res.rejected or 'rejected'}")
    await _write(conn, "UPDATE note_marks SET deleted_at = NULL WHERE id = ?",
                 (m["id"],))
    m["deleted_at"] = None
    await _update_block(conn, m)  # pieces may have changed while deleted
    return res


async def delete_mark(conn: aiosqlite.Connection, mark_id: str) -> ApplyResult:
    """Soft-delete the mark and its block; the user's other blocks stay."""
    async with tx(conn):
        return await _delete(conn, await _get(conn, mark_id))


async def restore_mark(conn: aiosqlite.Connection, mark_id: str,
                       ) -> ApplyResult:
    async with tx(conn):
        return await _restore(conn, await _get(conn, mark_id))


async def erase_piece(conn: aiosqlite.Connection, mark_id: str,
                      index: int) -> dict[str, Any]:
    """Erase one piece (kept flagged so Erase again / Undo can restore it).

    Erasing the last live piece deletes the mark too.
    """
    async with tx(conn):
        m = await _get(conn, mark_id)
        _check_index(m, index)
        if m["deleted_at"] is not None:
            raise MarkInputError("cannot erase from a deleted mark")
        m["pieces"][index] = {**m["pieces"][index], "erased": True}
        await _save_pieces(conn, m)
        if _live(m["pieces"]):
            await _update_block(conn, m)
        else:
            await _delete(conn, m)
        return await _get(conn, mark_id)


async def restore_piece(conn: aiosqlite.Connection, mark_id: str,
                        index: int) -> dict[str, Any]:
    """Undo an erase. A mark comes back only if erasing its last live piece
    is what removed it; one the user deleted stays deleted."""
    async with tx(conn):
        m = await _get(conn, mark_id)
        _check_index(m, index)
        auto_deleted = m["deleted_at"] is not None and not _live(m["pieces"])
        m["pieces"][index] = {k: v for k, v in m["pieces"][index].items()
                              if k != "erased"}
        await _save_pieces(conn, m)
        if auto_deleted:
            await _restore(conn, m)
        elif m["deleted_at"] is None:
            await _update_block(conn, m)
        return await _get(conn, mark_id)


async def split_last_piece(conn: aiosqlite.Connection, mark_id: str,
                           ) -> dict[str, Any]:
    """The newest piece becomes its own mark, right after the original.

    The moved piece stays in the original flagged ``removed`` so the other
    pieces' indexes do not shift.
    """
    async with tx(conn):
        m = await _get(conn, mark_id)
        live = [i for i, p in enumerate(m["pieces"])
                if not p.get("erased") and not p.get("removed")]
        if m["deleted_at"] is not None or len(live) < 2:
            raise MarkInputError("only a live mark with two pieces can split")
        piece = m["pieces"][live[-1]]
        m["pieces"][live[-1]] = {**piece, "removed": True}
        await _save_pieces(conn, m)
        await _update_block(conn, m)
        rec = {**m, "id": _mark_id(), "block_id": new_block_id(),
               "pieces": [piece], "status": "ok",
               "created_at": datetime.now(UTC).isoformat(),
               "deleted_at": None}
        await _insert_mark(conn, await _note_id(conn, m), rec, m["block_id"],
                           "Split mark")
        return await _get(conn, rec["id"])


async def colors(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    async with conn.execute(
        "SELECT color, name, visible FROM mark_colors WHERE owner_id = 1 "
        "ORDER BY rowid"
    ) as cur:
        rows = await cur.fetchall()
    return [{"color": r[0], "name": r[1], "visible": bool(r[2])}
            for r in rows]


async def set_color(conn: aiosqlite.Connection, color: str, *,
                    name: str | None = None,
                    visible: bool | None = None) -> None:
    async with conn.execute(
        "SELECT 1 FROM mark_colors WHERE owner_id = 1 AND color = ?", (color,)
    ) as cur:
        if await cur.fetchone() is None:
            raise LookupError(f"unknown colour {color}")
    if name is not None:
        await _write(conn, "UPDATE mark_colors SET name = ? "
                     "WHERE owner_id = 1 AND color = ?", (name, color))
    if visible is not None:
        await _write(conn, "UPDATE mark_colors SET visible = ? "
                     "WHERE owner_id = 1 AND color = ?",
                     (1 if visible else 0, color))
