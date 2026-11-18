"""Marks made on a translation (H1 bilingual reading): are their words still
in the paragraph's cached translation?

A cached translation holds ``⟦n⟧`` placeholders where H1 protected an inline
item (math, a reference, code) and ``⟦BR⟧`` at a page or column break. The
reader shows the item itself, so a marked quote carries its rendered text
there. Where the paragraph's placeholder map is known (a PDF paragraph: its
formulas, read as ``$TeX$`` as the card's quote reads them) each placeholder
is restored before matching. Otherwise (an HTML paragraph: the protected
nodes live in the page) a placeholder stands for its item's text, but only
*between* words the quote and the translation share: the quote must start
and end on translated words, and runs of its words must appear in order.
Never a wildcard that could swallow a whole quote.

A mark whose words are gone is ``translation_changed`` (its quote is kept);
found again, it is ``ok``. Checked on re-anchor and whenever H1 writes a
new translation of the block into the cache (``recheck_translation_marks``).
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.notes.mark_pieces import live_pieces
from paperhub.notes.marks import (
    BLOCK_TYPE,
    MARK_COLS,
    block_props,
    mark_from_row,
    write_row,
)
from paperhub.notes.ops import BlockIn, Op, _apply_in_tx
from paperhub.notes.papers import tx
from paperhub.notes.store import get_note
from paperhub.pipelines.paper_asset import read_block_assets
from paperhub.translation.pdf_paragraphs import build_paragraphs
from paperhub.translation.placeholders import normalize_language

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"⟦(\w+)⟧")
CHANGED = "translation_changed"

# A placeholder in the matching pattern (its text is unknown).
_ANY = None


def _restore(text: str, math: list[str] | None) -> str:
    """``⟦n⟧`` back to ``$TeX$`` where the map has it; ``⟦BR⟧`` to a space."""
    def sub(m: re.Match[str]) -> str:
        token = m.group(1)
        if token == "BR":
            return " "
        if math is not None and token.isdigit() and int(token) < len(math):
            return f"${math[int(token)]}$"
        return m.group(0)
    return _PLACEHOLDER.sub(sub, text)


def _pattern(text: str) -> list[str | None]:
    """The translation blind to whitespace: its characters, ``None`` for each
    placeholder still in it."""
    out: list[str | None] = []
    last = 0
    for m in _PLACEHOLDER.finditer(text):
        out.extend(c for c in text[last:m.start()] if not c.isspace())
        out.append(_ANY)
        last = m.end()
    out.extend(c for c in text[last:] if not c.isspace())
    return out


def in_translation(quote: str, text: str, math: list[str] | None = None) -> bool:
    """Whether a quote marked on a translation is in ``text`` (a cached
    translation), blind to whitespace. See the module docstring for how a
    placeholder is read."""
    q = [c for c in quote if not c.isspace()]
    if not q:
        return True
    n = len(q)
    # lit[i]: q[:i] matched, ending on a translated character just now;
    # gap[i]: q[:i] matched, the last pattern item a placeholder.
    lit = [False] * (n + 1)
    gap = [False] * (n + 1)
    for item in _pattern(_restore(text, math)):
        nlit = [False] * (n + 1)
        ngap = [False] * (n + 1)
        if item is _ANY:
            # It may hold any of the quote's characters, but only after a
            # translated one (a quote never starts inside it).
            run = False
            for i in range(1, n + 1):
                run = run or lit[i] or gap[i]
                ngap[i] = run
        else:
            for i in range(n):
                if q[i] == item and (i == 0 or lit[i] or gap[i]):
                    nlit[i + 1] = True
        lit, gap = nlit, ngap
        # It must end on a translated character.
        if lit[n]:
            return True
    return False


async def _paragraph_math(conn: aiosqlite.Connection, pcid: int,
                          ) -> dict[str, list[str]]:
    """Each PDF paragraph's placeholder map (key -> its formulas), from the
    paper's Marker blocks; empty when it has none."""
    async with conn.execute(
        "SELECT source_dir_path FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or not row[0]:
        return {}
    got = await asyncio.to_thread(read_block_assets, Path(str(row[0])))
    if got is None:
        return {}
    return {p.key: list(p.math) for p in build_paragraphs(got[0])}


class TranslationCheck:
    """Reads a paper's cached translations (and PDF placeholder maps) once,
    and judges its translation marks."""

    def __init__(self, conn: aiosqlite.Connection, pcid: int) -> None:
        self._conn = conn
        self._pcid = pcid
        self._math: dict[str, list[str]] | None = None

    async def _text(self, key: str, lang: str) -> str | None:
        async with self._conn.execute(
            "SELECT text FROM translations WHERE paper_content_id = ? "
            "AND block_key = ? AND language = ?",
            (self._pcid, key, normalize_language(lang)),
        ) as cur:
            row = await cur.fetchone()
        return str(row[0]) if row is not None else None

    async def status(self, m: dict[str, Any], base: str) -> str:
        """The status a translation mark should have, given the status its
        anchor implies (``base``). A lost anchor stays lost; a translation
        not in the cache is not judged (the mark keeps being changed or
        not)."""
        if not m["lang"] or not m["source_ref"] or base == "lost":
            return base
        text = await self._text(m["source_ref"], m["lang"])
        if text is None:
            return CHANGED if m["status"] == CHANGED else base
        math: list[str] | None = None
        if str(m["source_ref"]).startswith("p:"):
            if self._math is None:
                self._math = await _paragraph_math(self._conn, self._pcid)
            math = self._math.get(m["source_ref"])
        live = [p for p in live_pieces(m["pieces"]) if not p.get("lost")]
        found = all(in_translation(str(p.get("quote") or ""), text, math)
                    for p in live)
        return base if found else CHANGED


async def _note_of(conn: aiosqlite.Connection, pcid: int) -> int | None:
    async with conn.execute(
        "SELECT id FROM notes WHERE owner_id = 1 AND target_kind = 'paper' "
        "AND target_id = ? AND deleted_at IS NULL", (str(pcid),),
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else None


async def save_status(conn: aiosqlite.Connection, m: dict[str, Any],
                      status: str, note_id: int | None, summary: str) -> None:
    """A mark's new status and its block's props (an op, actor system).
    Runs inside the caller's transaction."""
    m["status"] = status
    await write_row(conn, "UPDATE note_marks SET status = ? WHERE id = ?",
                    (status, m["id"]))
    if m["deleted_at"] is None and note_id is not None:
        note = await get_note(conn, note_id)
        assert note is not None
        await _apply_in_tx(
            conn, note_id, base_revision=note.revision,
            ops=[Op(op="update", block_id=m["block_id"],
                    block=BlockIn(type=BLOCK_TYPE[m["kind"]],
                                  props=block_props(m)))],
            actor="system", run_id=None, summary=summary)


async def recheck_translation_marks(
    conn: aiosqlite.Connection, pcid: int, keys: list[str], language: str,
) -> list[str]:
    """After H1 wrote new translations of ``keys`` in ``language``: the marks
    made on them whose status changed (now ``translation_changed``, or ``ok``
    again), as ids. One write transaction; idempotent."""
    lang = normalize_language(language)
    wanted = set(keys)
    async with conn.execute(
        f"SELECT {MARK_COLS} FROM note_marks WHERE paper_content_id = ? "  # noqa: S608
        "AND lang IS NOT NULL AND source_ref IS NOT NULL", (pcid,),
    ) as cur:
        marks = [mark_from_row(r) for r in await cur.fetchall()]
    marks = [m for m in marks if m["source_ref"] in wanted
             and normalize_language(m["lang"]) == lang
             and m["status"] in ("ok", "partly_lost", CHANGED)]
    if not marks:
        return []
    check = TranslationCheck(conn, pcid)
    changed: list[str] = []
    note_id = await _note_of(conn, pcid)
    async with tx(conn):
        for m in marks:
            base = ("partly_lost" if any(p.get("lost") for p in live_pieces(m["pieces"]))
                    else "ok")
            status = await check.status(m, base)
            if status == m["status"]:
                continue
            found = status != CHANGED
            await save_status(conn, m, status, note_id,
                              "Translation found" if found else "Translation changed")
            changed.append(m["id"])
    return changed
