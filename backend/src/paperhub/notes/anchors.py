"""Keep reading marks and section headings attached across a re-ingest.

A re-ingest rebuilds a paper's chunks (new offsets and ids); a section rename
changes a heading's key. The user's marks and note headings must follow, and
never silently vanish: a quote that cannot be found again stays in the note,
flagged (``anchorLost`` / ``piecesLost``), so the user can still read it.

Coordinates: an HTML piece's ``chunk_start`` / ``chunk_end`` are offsets into
the paper's chunk texts joined with a newline in chunk-id order;
``chunk_ids`` lists the chunks that span touches. All block writes are ops
with actor ``system`` inside one transaction per paper.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from paperhub.notes.marks import (
    _BLOCK_TYPE,
    _COLS,
    _block_props,
    _live,
    _mark,
    _save_pieces,
    _write,
)
from paperhub.notes.ops import BlockIn, Op, _apply_in_tx
from paperhub.notes.papers import tx
from paperhub.notes.store import get_note, list_blocks

_Piece = dict[str, Any]
_Norm = tuple[str, list[int]]
_Span = tuple[int, int, int]


def _normalise(text: str) -> _Norm:
    """Collapse whitespace runs to one space; map each result char back."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            prev_space = True
        else:
            out.append(ch)
            prev_space = False
        idx.append(i)
    return "".join(out), idx


def _occurrences(text: str, needle: str) -> list[int]:
    found: list[int] = []
    start = text.find(needle)
    while start != -1:
        found.append(start)
        start = text.find(needle, start + 1)
    return found


def _common_suffix(a: str, b: str) -> int:
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


def _common_prefix(a: str, b: str) -> int:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def _pick(text: str, starts: list[int], length: int, piece: _Piece) -> int:
    """The best of several occurrences: matching context, then nearest."""
    prefix = _normalise(str(piece.get("prefix") or ""))[0]
    suffix = _normalise(str(piece.get("suffix") or ""))[0]
    old = int(piece.get("chunk_start") or 0)

    def score(s: int) -> tuple[int, int]:
        before = _normalise(text[max(0, s - len(prefix) - 8):s])[0]
        after = _normalise(text[s + length:s + length + len(suffix) + 8])[0]
        ctx = (_common_suffix(before, prefix) if prefix else 0) + (
            _common_prefix(after, suffix) if suffix else 0)
        return (-ctx, abs(s - old))

    return min(starts, key=score)


def _find(norm: _Norm, piece: _Piece) -> tuple[int, int] | None:
    """Span of the piece's quote in the chunk text, blind to whitespace runs."""
    quote = str(piece.get("orig") or piece.get("quote") or "")
    if not quote.strip():
        return None
    ntext, idx = norm  # a superset of the exact matches
    nquote = _normalise(quote.strip())[0]
    starts = _occurrences(ntext, nquote)
    if not starts:
        return None
    s = _pick(ntext, starts, len(nquote), piece)
    return idx[s], idx[s + len(nquote) - 1] + 1


async def _chunk_spans(conn: aiosqlite.Connection, pcid: int,
                       ) -> tuple[str, list[_Span]]:
    async with conn.execute(
        "SELECT id, text FROM chunks WHERE paper_content_id = ? ORDER BY id",
        (pcid,),
    ) as cur:
        rows = await cur.fetchall()
    parts: list[str] = []
    spans: list[_Span] = []
    pos = 0
    for cid, text in rows:
        t = str(text or "")
        spans.append((int(cid), pos, pos + len(t)))
        parts.append(t)
        pos += len(t) + 1
    return "\n".join(parts), spans


def _chunk_ids(spans: list[_Span], start: int, end: int) -> list[int]:
    return [cid for cid, s, e in spans if s < end and e > start]


async def _mark_rows(conn: aiosqlite.Connection, pcid: int, *,
                     html_only: bool) -> list[dict[str, Any]]:
    view = " AND view = 'html'" if html_only else ""
    async with conn.execute(
        f"SELECT {_COLS} FROM note_marks WHERE paper_content_id = ?{view} "  # noqa: S608
        "ORDER BY created_at, rowid", (pcid,),
    ) as cur:
        return [_mark(r) for r in await cur.fetchall()]


async def _system_update(conn: aiosqlite.Connection, note_id: int,
                         block_id: str, block: BlockIn, summary: str) -> None:
    note = await get_note(conn, note_id)
    assert note is not None
    await _apply_in_tx(
        conn, note_id, base_revision=note.revision,
        ops=[Op(op="update", block_id=block_id, block=block)],
        actor="system", run_id=None, summary=summary)


async def _find_note(conn: aiosqlite.Connection, pcid: int) -> int | None:
    async with conn.execute(
        "SELECT id FROM notes WHERE owner_id = 1 AND target_kind = 'paper' "
        "AND target_id = ? AND deleted_at IS NULL", (str(pcid),),
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else None


def _anchor(m: dict[str, Any], text: str, norm: _Norm, spans: list[_Span],
            ) -> tuple[list[_Piece], str, bool]:
    """New pieces, the status they imply and whether any offset moved."""
    pieces: list[_Piece] = []
    seen = lost = 0
    moved = False
    for p in m["pieces"]:
        if p.get("erased") or p.get("removed") or "chunk_start" not in p:
            pieces.append(p)
            continue
        seen += 1
        span = _find(norm, p)
        if span is None:
            lost += 1
            pieces.append({**p, "lost": True})
            continue
        s, e = span
        moved = moved or (s, e) != (p.get("chunk_start"), p.get("chunk_end"))
        pieces.append({**{k: v for k, v in p.items() if k != "lost"},
                       "chunk_start": s, "chunk_end": e,
                       "chunk_ids": _chunk_ids(spans, s, e)})
    if not seen:
        return pieces, m["status"], False
    if lost == seen:
        return pieces, "lost", moved
    if lost:
        return pieces, "partly_lost", moved
    keep = "translation_changed" if m["status"] == "translation_changed" else "ok"
    return pieces, keep, moved


async def reanchor_paper(conn: aiosqlite.Connection, paper_content_id: int,
                         *, dry_run: bool = False) -> dict[str, int]:
    """Re-find every HTML mark's quote in the paper's current chunks.

    Counts are per mark: ``ok`` (nothing moved), ``moved`` (all pieces found,
    some at new offsets), ``lost`` (no piece found), ``partly_lost``. PDF
    marks keep their geometry and are not counted. Idempotent.
    """
    stats = {"ok": 0, "moved": 0, "lost": 0, "partly_lost": 0}
    note_id = await _find_note(conn, paper_content_id)
    marks = await _mark_rows(conn, paper_content_id, html_only=True)
    if not marks and note_id is None:
        return stats
    text, spans = await _chunk_spans(conn, paper_content_id)
    norm = _normalise(text)
    async with tx(conn):
        for m in marks:
            if not any("chunk_start" in p for p in _live(m["pieces"])):
                continue
            pieces, status, moved = _anchor(m, text, norm, spans)
            key = status if status in ("lost", "partly_lost") else (
                "moved" if moved else "ok")
            stats[key] += 1
            if dry_run or (pieces == m["pieces"] and status == m["status"]):
                continue
            m["pieces"], m["status"] = pieces, status
            await _save_pieces(conn, m)
            await _write(conn, "UPDATE note_marks SET status = ? WHERE id = ?",
                         (status, m["id"]))
            if m["deleted_at"] is None and note_id is not None:
                await _system_update(
                    conn, note_id, m["block_id"],
                    BlockIn(type=_BLOCK_TYPE[m["kind"]],
                            props=_block_props(m)), "Re-anchor mark")
        if not dry_run and note_id is not None:
            await flag_lost_sections(conn, paper_content_id, note_id)
    return stats


async def flag_lost_sections(conn: aiosqlite.Connection, paper_content_id: int,
                             note_id: int) -> int:
    """Flag note headings whose section no longer exists in the paper.

    The heading and everything the user wrote under it stay; the heading just
    carries ``sectionLost``. A section that exists again clears the flag.
    """
    async with conn.execute(
        "SELECT DISTINCT section FROM chunks WHERE paper_content_id = ? "
        "AND section IS NOT NULL", (paper_content_id,),
    ) as cur:
        present = {str(r[0]) for r in await cur.fetchall()}
    changed = 0
    for b in await list_blocks(conn, note_id):
        if b.type != "section" or b.section_key is None:
            continue
        gone = b.section_key not in present
        if gone == bool(b.props.get("sectionLost")):
            continue
        props = {k: v for k, v in b.props.items() if k != "sectionLost"}
        if gone:
            props["sectionLost"] = True
        await _system_update(
            conn, note_id, b.id,
            BlockIn(type="section", props=props, content=b.content),
            "Section lost" if gone else "Section found")
        changed += 1
    return changed


async def rename_note_sections(conn: aiosqlite.Connection,
                               paper_content_id: int,
                               mapping: dict[str, str]) -> int:
    """Follow a section rename: heading key + title, the marks' section.

    Runs inside the caller's transaction. Returns the headings renamed.
    """
    note_id = await _find_note(conn, paper_content_id)
    if note_id is None:
        return 0
    renamed = 0
    for b in await list_blocks(conn, note_id):
        if b.type == "section" and b.section_key in mapping:
            new = mapping[b.section_key]
            await _system_update(
                conn, note_id, b.id,
                BlockIn(type="section", props={**b.props, "title": new},
                        content=b.content, section_key=new),
                "Section renamed")
            renamed += 1
    for m in await _mark_rows(conn, paper_content_id, html_only=False):
        if m["section_key"] not in mapping:
            continue
        m["section_key"] = mapping[m["section_key"]]
        await _write(conn, "UPDATE note_marks SET section_key = ? WHERE id = ?",
                     (m["section_key"], m["id"]))
        if m["deleted_at"] is None:
            await _system_update(
                conn, note_id, m["block_id"],
                BlockIn(type=_BLOCK_TYPE[m["kind"]], props=_block_props(m)),
                "Section renamed")
    await flag_lost_sections(conn, paper_content_id, note_id)
    return renamed
