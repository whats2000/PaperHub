"""Find text read off a PDF page in the paper's reading text (H5b.1).

The LaTeX-compiled PDF of an HTML paper has no Marker blocks, so a mark made
on it (and Follow on it) learns its section and chunk ids by finding the
page's text in the rendered HTML's reading text — the same text and chunk
spans the HTML reader's marks and their re-anchoring use
(``notes/html_text.py``, ``notes/anchors.py``).

The two sides differ: the PDF text layer has ligatures (``ﬁ``), words broken
across lines with a hyphen, and math as loose glyphs, where the reading text
has ``$TeX$``. So the PDF side is normalised (NFKC, end-of-line hyphens
joined, whitespace collapsed), both sides are compared word by word (case
and edge punctuation ignored), and math is a gap: a run of words is matched
at the start and another at the end of the text, in order, with math (and
the glyphs it read as) allowed in between.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.notes.html_text import TextIndex, build_text_index, normalise

_HYPHEN_EOL = re.compile(r"(\w)-[ \t]*\n\s*(\w)")
_MATH = re.compile(r"\$[^$]*\$")
_WORD = re.compile(r"\S+")
_EDGE = re.compile(r"^\W+|\W+$")
_CORE = re.compile(r"\w(?:.*\w)?", re.S)
#: Words in the run matched at each end.
ANCHOR_WORDS = 4
#: Fewer words than this are too little to place.
MIN_WORDS = 2
#: How far the end run may sit from where the word counts put it (words).
SLACK = 12

_Token = tuple[str | None, int, int]  # word (None: math), start, end


def pdf_normalise(text: str) -> str:
    """The text layer's text as the reading text would have it: ligatures
    unfolded (NFKC), a word broken across lines by a hyphen joined, and
    whitespace collapsed."""
    t = unicodedata.normalize("NFKC", text)
    return normalise(_HYPHEN_EOL.sub(r"\1\2", t))


def _word(raw: str) -> str:
    return _EDGE.sub("", unicodedata.normalize("NFKC", raw)).casefold()


def _tokens(text: str, *, math: bool) -> list[_Token]:
    """Words with their spans; with ``math``, each ``$…$`` is one gap."""
    out: list[_Token] = []
    pos = 0
    pieces = _MATH.finditer(text) if math else iter(())
    for m in pieces:
        out.extend(_words(text, pos, m.start()))
        out.append((None, m.start(), m.end()))
        pos = m.end()
    out.extend(_words(text, pos, len(text)))
    return out


def _words(text: str, start: int, end: int) -> list[_Token]:
    out: list[_Token] = []
    for m in _WORD.finditer(text, start, end):
        w = _word(m.group())
        if not w:
            continue
        # The span leaves out the edge punctuation the word ignores.
        core = _CORE.search(m.group())
        s, e = (m.start() + core.start(), m.start() + core.end()) if core else (m.start(), m.end())
        out.append((w, s, e))
    return out


def _occurrences(words: list[str | None], at: dict[str, list[int]],
                 gram: list[str]) -> list[int]:
    n = len(gram)
    return [i for i in at.get(gram[0], [])
            if words[i:i + n] == gram]


#: Query words one math gap may take up (the glyphs it read as).
MATH_WORDS = 12


def _extend(words: list[str | None], q: list[str], qi: int, ri: int, step: int) -> int:
    """Grows a matched run outward from query word ``qi`` / reading token
    ``ri`` (the first ones past it) while the words agree; a math gap takes
    up the query words up to the next agreeing one. Returns the last
    reading token taken."""
    last = ri - step
    while 0 <= qi < len(q) and 0 <= ri < len(words):
        if words[ri] == q[qi]:
            last, qi, ri = ri, qi + step, ri + step
            continue
        if words[ri] is not None:
            break
        nxt = ri + step
        target = words[nxt] if 0 <= nxt < len(words) else None
        taken = 0
        while 0 <= qi < len(q) and q[qi] != target and taken < MATH_WORDS:
            qi += step
            taken += 1
        if 0 <= qi < len(q) and q[qi] != target:
            break  # more than a formula's worth of words: not this place
        last, ri = ri, nxt
    return last


def match_text(reading: str, query: str) -> tuple[tuple[int, int] | None, dict[str, Any]]:
    """The span in ``reading`` of text read off a PDF page, or None; and how
    it was decided (for the response's ``_meta``)."""
    q = [w for w, _, _ in _tokens(pdf_normalise(query), math=False) if w]
    meta: dict[str, Any] = {"query_words": len(q), "matched": False}
    if len(q) < MIN_WORDS:
        meta["reason"] = "too_short"
        return None, meta
    toks = _tokens(reading, math=True)
    words = [w for w, _, _ in toks]
    at: dict[str, list[int]] = {}
    for i, w in enumerate(words):
        if w is not None:
            at.setdefault(w, []).append(i)
    k = min(ANCHOR_WORDS, len(q))
    lead = next(((i, occ) for i in range(len(q) - k + 1)
                 if (occ := _occurrences(words, at, q[i:i + k]))), None)
    if lead is None:
        meta["reason"] = "no_start"
        return None, meta
    i, starts = lead
    trail = next(((j, occ) for j in range(len(q) - k, i - 1, -1)
                  if (occ := _occurrences(words, at, q[j:j + k]))), None)
    assert trail is not None  # the start run itself qualifies
    j, ends = trail
    want = j - i  # words between the runs in the query
    pairs = [(abs((b - a) - want), a, b) for a in starts for b in ends
             if b >= a and (b - a) <= want + SLACK]
    if not pairs:
        meta["reason"] = "runs_apart"
        return None, meta
    pairs.sort()
    _, a, b = pairs[0]
    # The runs grow outward over the query's remaining words and math.
    first = _extend(words, q, i - 1, a - 1, -1)
    last = _extend(words, q, j + k, b + k, 1)
    meta.update(matched=True, start_word=i, end_word=j, candidates=len(pairs),
                ambiguous=len(pairs) > 1 and pairs[1][0] == pairs[0][0])
    return (toks[first][1], toks[last][2]), meta


@lru_cache(maxsize=8)
def _index(path: str, mtime_ns: int) -> TextIndex:
    del mtime_ns  # part of the cache key: a re-render reads the file again
    return build_text_index(Path(path).read_text(encoding="utf-8"))


async def reading_index(conn: aiosqlite.Connection, pcid: int,
                        ) -> tuple[TextIndex, dict[str, int]] | None:
    """The paper's reading text (cached per file version) and its spans'
    chunk ids; None without a rendered HTML whose spans name the chunks."""
    async with conn.execute(
        "SELECT html_path FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or not row[0]:
        return None
    async with conn.execute(
        "SELECT dom_id, id FROM chunks WHERE paper_content_id = ? "
        "AND dom_id IS NOT NULL", (pcid,),
    ) as cur:
        ids = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
    path = str(row[0])
    try:
        mtime = (await asyncio.to_thread(Path(path).stat)).st_mtime_ns
        index = await asyncio.to_thread(_index, path, mtime)
    except OSError:
        return None
    if not ids or not any(d in ids for d, _ in index.spans):
        return None
    return index, ids


def chunks_in(index: TextIndex, ids: dict[str, int], s: int, e: int) -> list[int]:
    """The chunks a span of the reading text covers, in order."""
    out: list[int] = []
    for dom_id, pos in index.spans:
        cid = ids.get(dom_id)
        if cid is None:
            continue
        if pos <= s:
            out = [cid]
        elif pos < e:
            out.append(cid)
        else:
            break
    return out


async def locate_text(conn: aiosqlite.Connection, pcid: int, text: str,
                      ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Where text read off a PDF page sits in the paper: its chunks, the
    section of the first, and its span in the reading text; None when it
    is not found. The second value records the decision."""
    reading = await reading_index(conn, pcid)
    if reading is None:
        return None, {"source": "html_text", "matched": False, "reason": "no_reading_text"}
    index, ids = reading
    span, meta = match_text(index.text, text)
    meta["source"] = "html_text"
    if span is None:
        return None, meta
    s, e = span
    covered = chunks_in(index, ids, s, e)
    if not covered:
        meta.update(matched=False, reason="no_chunk")
        return None, meta
    async with conn.execute("SELECT section FROM chunks WHERE id = ?", (covered[0],)) as cur:
        row = await cur.fetchone()
    return {
        "chunk_start": covered[0], "chunk_end": covered[-1], "chunk_ids": covered,
        "section": str(row[0]) if row and row[0] else None,
        "text_start": s, "text_end": e,
    }, meta
