"""Reading marks on papers: text, figures, equations, regions.

A mark's ``kind`` says WHAT was marked; a text mark's ``style`` says how it
is drawn (highlight / underline / wavy / bar). ``color`` is a palette key
(``mark_colors``) or None (no colour: a neutral grey; a highlight needs one).

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
from paperhub.notes.dehyphen import settle_pieces
from paperhub.notes.mark_colors import (
    BUILTIN,
    clean_name,
    color_problem,
    dark_variant,
)
from paperhub.notes.mark_pieces import erased, live_pieces, revive_latest, unerased
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

BLOCK_TYPE: dict[str, Any] = {
    "text": "paperQuote", "figure": "paperFigure",
    "equation": "paperEquation", "region": "paperRegion",
}
STYLES = ("highlight", "underline", "wavy", "bar")
# Before the kind + style split (2026-11-19): what an old kind meant.
LEGACY_KINDS = {"highlight": ("text", "highlight"),
                "quote": ("text", "underline")}
_VIEWS = ("html", "pdf")
MARK_COLS = ("id, paper_content_id, block_id, kind, color, view, pieces_json, "
         "lang, source_ref, section_key, status, created_at, deleted_at, style")
_INSERT = (
    "INSERT INTO note_marks (id, paper_content_id, block_id, kind, color, "
    "view, pieces_json, lang, source_ref, section_key, status, created_at, "
    "style) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?, ?)")


class MarkInputError(ValueError):
    """The request names something invalid (kind, view, piece, ...)."""


class MarkConflict(RuntimeError):
    """The mark's block could not be changed (e.g. the user edited it)."""


class MarkIn(BaseModel):
    paper_content_id: int
    kind: str
    style: str | None = None
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


def mark_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row[0], "paper_content_id": row[1], "block_id": row[2],
        "kind": row[3], "color": row[4], "view": row[5],
        "pieces": json.loads(row[6]), "lang": row[7], "source_ref": row[8],
        "section_key": row[9], "status": row[10], "created_at": row[11],
        "deleted_at": row[12], "style": row[13],
    }


async def _get(conn: aiosqlite.Connection, mark_id: str) -> dict[str, Any]:
    async with conn.execute(
        f"SELECT {MARK_COLS} FROM note_marks WHERE id = ?", (mark_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"mark {mark_id} not found")
    return mark_from_row(row)


async def get_mark(conn: aiosqlite.Connection, mark_id: str) -> dict[str, Any]:
    """A mark as stored (deleted ones too); LookupError when unknown."""
    return await _get(conn, mark_id)


async def write_row(conn: aiosqlite.Connection, sql: str,
                 params: tuple[Any, ...]) -> None:
    async with write_lock(conn):
        await conn.execute(sql, params)
        if not in_write_transaction():
            await conn.commit()


_FAR = float("inf")


def _num(v: Any, default: float = _FAR) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else default


def _top_left(p: dict[str, Any]) -> tuple[float, float]:
    boxes = p.get("rects") if isinstance(p.get("rects"), list) else None
    boxes = boxes or ([p["box"]] if isinstance(p.get("box"), list) else [])
    if not boxes:
        return _FAR, _FAR
    return (min(_num(b[1]) for b in boxes), min(_num(b[0]) for b in boxes))


def _reading_key(p: dict[str, Any], pdf: bool) -> tuple[float, ...]:
    # Pieces of one translated paragraph share their source's place: they
    # read by where they sit in the translation (``tr_start``).
    tr = _num(p.get("tr_start"), 0.0)
    if pdf:
        return (_num(p.get("page")), *_top_left(p), tr)
    return (_num(p.get("chunk_start")), _num(p.get("text_start"), 0.0), tr)


def reading_order(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pieces as the paper reads them, for showing only (the stored list
    stays in stroke order: erase, restore and split address pieces by
    index, and the last live one is the newest). A PDF piece (it has a
    page) reads by page, then top, then left; an HTML piece by its chunk,
    then its offset in the reading text. Mirrors the frontend's
    ``readingOrder`` (lib/marks/pieces.ts)."""
    pdf = bool(pieces) and all(isinstance(p.get("page"), int) for p in pieces)
    return sorted(pieces, key=lambda p: _reading_key(p, pdf))


def joined_quote(pieces: list[dict[str, Any]]) -> str:
    """One quote for a mark's live pieces in reading order. Pieces of one
    stroke (a selection across paragraphs) are joined by a paragraph break;
    pieces of strokes joined later by "…", which stands for skipped words
    only. A piece with no ``stroke`` is a stroke of its own. Mirrors the
    frontend's ``joinedQuote``."""
    out = ""
    prev: object = None
    for i, p in enumerate(reading_order(pieces)):
        q = str(p.get("quote") or "").strip()
        if not q:
            continue
        s = p.get("stroke")
        # Only a whole number names a stroke (as the frontend reads it);
        # null, a string or a bool make the piece a stroke of its own.
        whole = (isinstance(s, (int, float)) and not isinstance(s, bool)
                 and float(s).is_integer())
        stroke: object = s if whole else ("own", i)
        if out:
            out += "\n" if stroke == prev else " … "
        out += q
        prev = stroke
    return out


def _stroked(pieces: list[dict[str, Any]], stroke: int) -> list[dict[str, Any]]:
    """The pieces of one save (a selection or a stroke), tagged with it."""
    return [{**p, "stroke": stroke} for p in pieces]


def _check_index(m: dict[str, Any], index: int) -> None:
    if not 0 <= index < len(m["pieces"]):
        raise MarkInputError(f"piece {index} out of range")
    if m["pieces"][index].get("removed"):
        raise MarkInputError(f"piece {index} belongs to another mark now")


def normalized(kind: str, style: str | None) -> tuple[str, str | None]:
    """(kind, style) as stored: an old kind becomes text in its style; a
    figure / equation / region mark has no style."""
    if kind in LEGACY_KINDS:
        return LEGACY_KINDS[kind]
    if kind not in BLOCK_TYPE:
        raise MarkInputError(f"unknown mark kind {kind!r}")
    if kind != "text":
        return kind, None
    if style not in STYLES:
        raise MarkInputError(f"unknown mark style {style!r}")
    return kind, style


async def _check_color(conn: aiosqlite.Connection, style: str | None,
                       color: str | None) -> None:
    if color is None:
        if style == "highlight":
            raise MarkInputError("a highlight needs a colour")
        return
    async with conn.execute(
        "SELECT 1 FROM mark_colors WHERE owner_id = 1 AND color = ?", (color,)
    ) as cur:
        if await cur.fetchone() is None:
            raise MarkInputError(f"unknown colour {color!r}")


def _validate(kind: str, view: str, pieces: list[dict[str, Any]]) -> None:
    if kind not in BLOCK_TYPE:
        raise MarkInputError(f"unknown mark kind {kind!r}")
    if view not in _VIEWS:
        raise MarkInputError(f"unknown mark view {view!r}")
    if not pieces:
        raise MarkInputError("a mark needs at least one piece")


def block_props(m: dict[str, Any]) -> dict[str, Any]:
    live = live_pieces(m["pieces"])
    first = live[0] if live else {}
    source: dict[str, Any] = {}
    if first.get("page") is not None:
        source["page"] = first["page"]
    if m["section_key"]:
        source["section"] = m["section_key"]
    props: dict[str, Any] = {
        "markId": m["id"], "color": m["color"], "kind": m["kind"],
        "quote": joined_quote(live),
        "pieces": live, "source": source,
    }
    if m.get("style"):
        props["style"] = m["style"]
    lost = sum(1 for p in live if p.get("lost"))
    if m["status"] == "lost":
        props["anchorLost"] = True
    elif lost:
        # Also on a translation mark whose words changed as well.
        props["piecesLost"] = lost
    if m["lang"]:
        props["lang"] = m["lang"]
    if m["source_ref"]:
        props["sourceRef"] = m["source_ref"]
    if m["status"] == "translation_changed":
        props["translationChanged"] = True
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
           block=BlockIn(type=BLOCK_TYPE[m["kind"]],
                         props=block_props(m))),
        "Edit mark", m["block_id"])


async def save_pieces(conn: aiosqlite.Connection, m: dict[str, Any]) -> None:
    await write_row(conn, "UPDATE note_marks SET pieces_json = ? WHERE id = ?",
                 (json.dumps(m["pieces"]), m["id"]))


async def _insert_mark(conn: aiosqlite.Connection, note_id: int,
                       rec: dict[str, Any], after: str | None,
                       summary: str) -> None:
    await _apply(
        conn, note_id,
        Op(op="insert", block_id=rec["block_id"], after=after,
           block=BlockIn(type=BLOCK_TYPE[rec["kind"]],
                         props=block_props(rec))),
        summary, rec["block_id"])
    await write_row(conn, _INSERT, (
        rec["id"], rec["paper_content_id"], rec["block_id"], rec["kind"],
        rec["color"], rec["view"], json.dumps(rec["pieces"]), rec["lang"],
        rec["source_ref"], rec["section_key"], rec["created_at"],
        rec.get("style")))


async def create_mark(conn: aiosqlite.Connection, m: MarkIn,
                      ) -> tuple[dict[str, Any], SavedTo]:
    kind, style = normalized(m.kind, m.style)
    m = m.model_copy(update={"kind": kind, "style": style})
    _validate(m.kind, m.view, m.pieces)
    await _check_color(conn, m.style, m.color)
    # A PDF piece's quote is made here from its raw lines: the paper's
    # words decide each line-end hyphen.
    m = m.model_copy(update={"pieces": await settle_pieces(
        conn, m.paper_content_id, m.pieces)})
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
            "block_id": new_block_id(), "kind": m.kind, "style": m.style,
            "color": m.color,
            "view": m.view, "pieces": _stroked(m.pieces, 0), "lang": m.lang,
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
    if (rec["paper_content_id"], rec["color"], rec["kind"], rec["style"],
            rec["view"]) != (m.paper_content_id, m.color, m.kind, m.style,
                             m.view):
        raise MarkInputError("a piece can only join a mark of the same paper, "
                         "colour, kind, style and view")
    # A later stroke: the next stroke number (its pieces read after "…").
    nxt = 1 + max((int(p.get("stroke", 0)) for p in rec["pieces"]), default=-1)
    rec["pieces"] = [*rec["pieces"], *_stroked(m.pieces, nxt)]
    await save_pieces(conn, rec)
    await _update_block(conn, rec)
    return rec, await _saved_to(conn, rec)


async def list_marks(conn: aiosqlite.Connection,
                     paper_content_id: int) -> list[dict[str, Any]]:
    async with conn.execute(
        f"SELECT {MARK_COLS} FROM note_marks WHERE paper_content_id = ? "
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
        m = mark_from_row(r)
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
    await write_row(conn, "UPDATE note_marks SET deleted_at = ? WHERE id = ?",
                 (datetime.now(UTC).isoformat(), m["id"]))
    m["deleted_at"] = "now"
    return res


async def _restore(conn: aiosqlite.Connection, m: dict[str, Any],
                   ) -> ApplyResult:
    # A mark whose last piece was erased comes back with that piece.
    pieces = revive_latest(m["pieces"])
    if pieces is None:
        raise MarkInputError(f"mark {m['id']} has no piece to bring back")
    if pieces is not m["pieces"]:
        m["pieces"] = pieces
        await save_pieces(conn, m)
    res = await _restore_in_tx(conn, await _note_id(conn, m),
                               [m["block_id"]], actor="user")
    if m["block_id"] not in res.applied:
        raise MarkConflict(
            f"block {m['block_id']} was not restored: "
            f"{res.rejected or 'rejected'}")
    await write_row(conn, "UPDATE note_marks SET deleted_at = NULL WHERE id = ?",
                 (m["id"],))
    m["deleted_at"] = None
    await _update_block(conn, m)  # pieces may have changed while deleted
    return res


async def _unchanged(conn: aiosqlite.Connection,
                     m: dict[str, Any]) -> ApplyResult:
    """The answer to a delete / restore that finds the mark already so:
    what a delete / restore that just landed says, with nothing written."""
    note = await paper_note(conn, m["paper_content_id"])
    return ApplyResult(note_id=note.id, revision=note.revision,
                       change_set_id=None, applied=[m["block_id"]],
                       suggested=[], rejected=[])


async def delete_mark(conn: aiosqlite.Connection, mark_id: str) -> ApplyResult:
    """Soft-delete the mark and its block; the user's other blocks stay.

    A mark already deleted (its block deleted in the note first) is a quiet
    success: the reader's Delete may cross the editor's on the way."""
    async with tx(conn):
        m = await _get(conn, mark_id)
        if m["deleted_at"] is not None:
            return await _unchanged(conn, m)
        return await _delete(conn, m)


async def restore_mark(conn: aiosqlite.Connection, mark_id: str,
                       ) -> ApplyResult:
    """Bring the mark and its block back; one already live (the editor's
    undo got there first) is a quiet success."""
    async with tx(conn):
        m = await _get(conn, mark_id)
        if m["deleted_at"] is None:
            return await _unchanged(conn, m)
        return await _restore(conn, m)


async def erase_piece(conn: aiosqlite.Connection, mark_id: str,
                      index: int) -> dict[str, Any]:
    """Erase one piece (kept flagged so Erase again / Undo can restore it).

    Erasing the last live piece deletes the mark too.
    """
    async with tx(conn):
        m = await _get(conn, mark_id)
        _check_index(m, index)
        if m["deleted_at"] is not None:
            # Deleted elsewhere (its block in the note): a stale view.
            raise MarkConflict(f"mark {mark_id} is deleted")
        if m["pieces"][index].get("erased"):
            return m  # erased already: nothing to do
        m["pieces"][index] = erased(m["pieces"][index], m["pieces"])
        await save_pieces(conn, m)
        if live_pieces(m["pieces"]):
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
        auto_deleted = m["deleted_at"] is not None and not live_pieces(m["pieces"])
        m["pieces"][index] = unerased(m["pieces"][index])
        await save_pieces(conn, m)
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
            # Split is offered for two live pieces: anything else is the
            # mark changed elsewhere (a stale view), not bad input.
            raise MarkConflict("only a live mark with two pieces can split")
        piece = m["pieces"][live[-1]]
        m["pieces"][live[-1]] = {**piece, "removed": True}
        await save_pieces(conn, m)
        await _update_block(conn, m)
        rec = {**m, "id": _mark_id(), "block_id": new_block_id(),
               "pieces": [piece], "status": "ok",
               "created_at": datetime.now(UTC).isoformat(),
               "deleted_at": None}
        await _insert_mark(conn, await _note_id(conn, m), rec, m["block_id"],
                           "Split mark")
        return await _get(conn, rec["id"])


async def colors(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    """The palette in the user's order: key, label, hex (light), dark,
    visible, builtin (the four defaults cannot be deleted)."""
    async with conn.execute(
        "SELECT color, name, visible, hex FROM mark_colors WHERE owner_id = 1 "
        "ORDER BY sort_order, rowid"
    ) as cur:
        rows = await cur.fetchall()
    return [{"color": r[0], "name": r[1], "visible": bool(r[2]), "hex": r[3],
             "dark": dark_variant(r[0], r[3]) if r[3] else r[3],
             "builtin": r[0] in BUILTIN} for r in rows]


async def color_labels(conn: aiosqlite.Connection) -> dict[str, str]:
    """Palette key -> the user's label for it."""
    async with conn.execute(
            "SELECT color, name FROM mark_colors WHERE owner_id = 1") as cur:
        return {str(r[0]): str(r[1]) for r in await cur.fetchall()}


async def _unique_name(conn: aiosqlite.Connection, name: str,
                       except_key: str | None = None) -> str:
    try:
        n = clean_name(name)
    except ValueError as exc:
        raise MarkInputError(str(exc)) from exc
    for c in await colors(conn):
        if c["color"] != except_key and c["name"].casefold() == n.casefold():
            raise MarkInputError(f"another colour is already called {n!r}")
    return n


def _good_hex(hex_: str) -> str:
    problem = color_problem(hex_)
    if problem:
        raise MarkInputError(f"colour {hex_}: {problem}")
    return hex_.lower()


async def _known(conn: aiosqlite.Connection, color: str) -> None:
    async with conn.execute(
        "SELECT 1 FROM mark_colors WHERE owner_id = 1 AND color = ?", (color,)
    ) as cur:
        if await cur.fetchone() is None:
            raise LookupError(f"unknown colour {color}")


async def add_color(conn: aiosqlite.Connection, *, hex: str,  # noqa: A002
                    name: str) -> dict[str, Any]:
    """A new palette row at the end; the label is required and unique."""
    n = await _unique_name(conn, name)
    h = _good_hex(hex)
    key = "c_" + "".join(secrets.choice(string.ascii_lowercase + string.digits)
                         for _ in range(8))
    async with conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM mark_colors "
        "WHERE owner_id = 1") as cur:
        order = int((await cur.fetchone() or (0,))[0])
    await write_row(conn, "INSERT INTO mark_colors (owner_id, color, name, "
                    "visible, hex, sort_order) VALUES (1, ?, ?, 1, ?, ?)",
                    (key, n, h, order))
    return next(c for c in await colors(conn) if c["color"] == key)


async def set_color(conn: aiosqlite.Connection, color: str, *,
                    name: str | None = None,
                    visible: bool | None = None,
                    hex: str | None = None) -> None:  # noqa: A002
    await _known(conn, color)
    if name is not None:
        await write_row(conn, "UPDATE mark_colors SET name = ? "
                     "WHERE owner_id = 1 AND color = ?",
                     (await _unique_name(conn, name, color), color))
    if visible is not None:
        await write_row(conn, "UPDATE mark_colors SET visible = ? "
                     "WHERE owner_id = 1 AND color = ?",
                     (1 if visible else 0, color))
    if hex is not None:
        await write_row(conn, "UPDATE mark_colors SET hex = ? "
                     "WHERE owner_id = 1 AND color = ?",
                     (_good_hex(hex), color))


async def delete_color(conn: aiosqlite.Connection, color: str) -> None:
    """Remove a custom colour no mark uses (live or deleted: Undo may bring
    a deleted mark back)."""
    await _known(conn, color)
    if color in BUILTIN:
        raise MarkInputError("a default colour can be hidden, not deleted")
    async with conn.execute(
            "SELECT COUNT(*) FROM note_marks WHERE color = ?", (color,)) as cur:
        used = int((await cur.fetchone() or (0,))[0])
    if used:
        raise MarkInputError(f"{used} mark(s) use this colour")
    await write_row(conn, "DELETE FROM mark_colors WHERE owner_id = 1 "
                    "AND color = ?", (color,))


async def reorder_colors(conn: aiosqlite.Connection,
                         order: list[str]) -> None:
    """The palette in this order (every colour named exactly once)."""
    have = [c["color"] for c in await colors(conn)]
    if sorted(order) != sorted(have) or len(set(order)) != len(order):
        raise MarkInputError("the order must name every colour once")
    async with tx(conn):
        for i, key in enumerate(order):
            await write_row(conn, "UPDATE mark_colors SET sort_order = ? "
                            "WHERE owner_id = 1 AND color = ?", (i, key))
