"""Keep reading marks and section headings attached across a re-ingest.

A re-ingest rebuilds a paper's chunks (new offsets and ids); a section rename
changes a heading's key. The user's marks and note headings must follow, and
never silently vanish: a quote that cannot be found again stays in the note,
flagged (``anchorLost`` / ``piecesLost``), so the user can still read it.

Where a quote is looked for: the paper's rendered HTML (``source.html``),
read with the reader's own text rule (``notes/html_text.py``: math as
``$TeX$``, translations and overlays left out). The reader quoted that text;
``chunks.text`` is LaTeX source (``\%``, ``\ref``, macros) and would not match.

Coordinates: an HTML piece's ``chunk_start`` / ``chunk_end`` are CHUNK IDS (the
chunks whose ``phchunk-N`` spans open last before the quote's first and last
character). ``text_start`` / ``text_end`` are the quote's span in that text,
used as the proximity hint when the same quote repeats. A paper whose chunks
have no spans yet (re-chunked, HTML not re-rendered) is left as it is: the
re-anchor after the re-render places its marks. All block writes are ops with
actor ``system`` inside one transaction per paper.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.notes.html_text import TextIndex, build_text_index
from paperhub.notes.marks import (
    BLOCK_TYPE,
    MARK_COLS,
    block_props,
    live_pieces,
    mark_from_row,
    save_pieces,
    write_row,
)
from paperhub.notes.ops import BlockIn, Op, _apply_in_tx
from paperhub.notes.papers import tx
from paperhub.notes.store import get_note, list_blocks

_Piece = dict[str, Any]
_Norm = tuple[str, list[int]]


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


def _pick(text: str, starts: list[int], length: int, piece: _Piece,
          hint: int) -> int:
    """The best of several occurrences: matching context, then nearest."""
    prefix = _normalise(str(piece.get("prefix") or ""))[0]
    suffix = _normalise(str(piece.get("suffix") or ""))[0]
    old = hint

    def score(s: int) -> tuple[int, int]:
        before = _normalise(text[max(0, s - len(prefix) - 8):s])[0]
        after = _normalise(text[s + length:s + length + len(suffix) + 8])[0]
        ctx = (_common_suffix(before, prefix) if prefix else 0) + (
            _common_prefix(after, suffix) if suffix else 0)
        return (-ctx, abs(s - old))

    return min(starts, key=score)


def _find(index: TextIndex, piece: _Piece, hint: int,
          ) -> tuple[int, int] | None:
    """Span of the piece's quote in the reading text: exact, then blind to
    whitespace; among repeats the best context, then the nearest."""
    quote = str(piece.get("orig") or piece.get("quote") or "")
    if not quote.strip():
        return None
    text = index.text
    for needle in (quote.strip(), _normalise(quote.strip())[0]):
        starts = _occurrences(text, needle)
        if starts:
            s = _pick(text, starts, len(needle), piece, hint)
            return s, s + len(needle)
    return None


async def _reading_text(conn: aiosqlite.Connection, pcid: int,
                        ) -> tuple[TextIndex, dict[str, int]] | None:
    """The rendered paper's reading text and its spans' chunk ids; None when
    the HTML is missing or its spans don't name the current chunks yet."""
    async with conn.execute(
        "SELECT html_path FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    async with conn.execute(
        "SELECT dom_id, id FROM chunks WHERE paper_content_id = ? "
        "AND dom_id IS NOT NULL", (pcid,),
    ) as cur:
        ids = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
    if row is None or not row[0] or not ids:
        return None
    try:
        html = await asyncio.to_thread(Path(str(row[0])).read_text, encoding="utf-8")
    except OSError:
        return None
    index = build_text_index(html)
    if not any(d in ids for d, _ in index.spans):
        return None
    return index, ids


def _chunk_at(index: TextIndex, ids: dict[str, int], at: int) -> int | None:
    """The chunk whose span opens last at or before ``at`` (else the first)."""
    first: int | None = None
    found: int | None = None
    for dom_id, pos in index.spans:
        cid = ids.get(dom_id)
        if cid is None:
            continue
        first = cid if first is None else first
        if pos > at:
            break
        found = cid
    return found if found is not None else first


def _hint(index: TextIndex, ids: dict[str, int], piece: _Piece) -> int:
    """Where the quote was: its last offset, else its chunk's span."""
    if isinstance(piece.get("text_start"), int):
        return int(piece["text_start"])
    dom = next((d for d, cid in ids.items() if cid == piece.get("chunk_start")), None)
    return next((pos for d, pos in index.spans if d == dom), 0)


async def _mark_rows(conn: aiosqlite.Connection, pcid: int, *,
                     html_only: bool) -> list[dict[str, Any]]:
    view = " AND view = 'html'" if html_only else ""
    async with conn.execute(
        f"SELECT {MARK_COLS} FROM note_marks WHERE paper_content_id = ?{view} "  # noqa: S608
        "ORDER BY created_at, rowid", (pcid,),
    ) as cur:
        return [mark_from_row(r) for r in await cur.fetchall()]


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


def _anchor(m: dict[str, Any], index: TextIndex, ids: dict[str, int],
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
        span = _find(index, p, _hint(index, ids, p))
        if span is None:
            lost += 1
            pieces.append({**p, "lost": True})
            continue
        s, e = span
        first, last = _chunk_at(index, ids, s), _chunk_at(index, ids, e - 1)
        moved = moved or (first, last) != (p.get("chunk_start"),
                                           p.get("chunk_end"))
        pieces.append({**{k: v for k, v in p.items() if k != "lost"},
                       "chunk_start": first, "chunk_end": last,
                       "text_start": s, "text_end": e})
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
    """Re-find every HTML mark's quote in the paper's rendered text.

    Counts are per mark: ``ok`` (nothing moved), ``moved`` (all pieces found,
    some at new offsets), ``lost`` (no piece found), ``partly_lost``. PDF
    marks keep their geometry and are not counted. Idempotent.
    """
    stats = {"ok": 0, "moved": 0, "lost": 0, "partly_lost": 0}
    note_id = await _find_note(conn, paper_content_id)
    marks = await _mark_rows(conn, paper_content_id, html_only=True)
    if not marks and note_id is None:
        return stats
    # Not rendered for these chunks yet: the marks wait for the re-render.
    reading = await _reading_text(conn, paper_content_id)
    async with tx(conn):
        for m in marks if reading is not None else []:
            assert reading is not None
            index, ids = reading
            if not any("chunk_start" in p for p in live_pieces(m["pieces"])):
                continue
            pieces, status, moved = _anchor(m, index, ids)
            key = status if status in ("lost", "partly_lost") else (
                "moved" if moved else "ok")
            stats[key] += 1
            if dry_run or (pieces == m["pieces"] and status == m["status"]):
                continue
            m["pieces"], m["status"] = pieces, status
            await save_pieces(conn, m)
            await write_row(conn, "UPDATE note_marks SET status = ? WHERE id = ?",
                         (status, m["id"]))
            if m["deleted_at"] is None and note_id is not None:
                await _system_update(
                    conn, note_id, m["block_id"],
                    BlockIn(type=BLOCK_TYPE[m["kind"]],
                            props=block_props(m)), "Re-anchor mark")
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
        await write_row(conn, "UPDATE note_marks SET section_key = ? WHERE id = ?",
                     (m["section_key"], m["id"]))
        if m["deleted_at"] is None:
            await _system_update(
                conn, note_id, m["block_id"],
                BlockIn(type=BLOCK_TYPE[m["kind"]], props=block_props(m)),
                "Section renamed")
    await flag_lost_sections(conn, paper_content_id, note_id)
    return renamed
