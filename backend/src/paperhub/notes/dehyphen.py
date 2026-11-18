"""Words broken across PDF lines by a hyphen, joined by the paper's own
words (H5b.1).

The text layer cannot tell ``effec-`` / ``tive`` (one word broken to fit
the line) from ``self-`` / ``supervised`` (a compound that happened to end
a line). The paper can: the joined form, or the hyphenated one, is among
its words. Those come from the HTML reading text for an HTML paper and from
the chunks' text for a PDF-only one, counted once per paper version.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.notes.locate import text_index

_EOL_HYPHEN = re.compile(r"(\w+)-[ \t]*\n\s*(\w+)")
_WORD = re.compile(r"\S+")
_EDGE = re.compile(r"^\W+|\W+$")

_cache: dict[int, tuple[tuple[object, ...], Counter[str]]] = {}


def clear_cache() -> None:
    _cache.clear()


def _key(word: str) -> str:
    return _EDGE.sub("", unicodedata.normalize("NFKC", word)).casefold()


def count_words(text: str) -> Counter[str]:
    return Counter(k for m in _WORD.finditer(text) if (k := _key(m.group())))


def dehyphen(raw: str, words: Counter[str]) -> str:
    """``raw`` (the text layer's text, a line break between lines) with
    each line-end hyphen decided by ``words``: joined when the paper has
    the joined word, kept when it has the hyphenated one (the more frequent
    form when it has both), else kept without the line break. Whitespace is
    collapsed."""
    def join(m: re.Match[str]) -> str:
        a, b = m[1], m[2]
        joined, hyph = words[_key(a + b)], words[_key(f"{a}-{b}")]
        return f"{a}{b}" if joined and joined >= hyph else f"{a}-{b}"
    return " ".join(_EOL_HYPHEN.sub(join, unicodedata.normalize("NFKC", raw)).split())


async def paper_words(conn: aiosqlite.Connection, pcid: int) -> Counter[str]:
    """The paper's words with their counts: the HTML reading text of a
    paper rendered from LaTeX, the chunks' text of a PDF-only one (its HTML
    is itself read off the PDF). Cached per paper and version."""
    async with conn.execute(
        "SELECT html_path, source_path FROM paper_content WHERE id = ?", (pcid,),
    ) as cur:
        row = await cur.fetchone()
    pdf_only = bool(row and row[1] and str(row[1]).lower().endswith(".pdf"))
    html = str(row[0]) if row and row[0] and not pdf_only else None
    mtime: int | None = None
    if html is not None:
        try:
            mtime = (await asyncio.to_thread(Path(html).stat)).st_mtime_ns
        except OSError:
            html = None
    if html is not None and mtime is not None:
        key: tuple[object, ...] = ("html", html, mtime)
    else:
        async with conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks "
            "WHERE paper_content_id = ?", (pcid,),
        ) as cur:
            n = await cur.fetchone()
        key = ("chunks", *(n or (0, 0)))
    hit = _cache.get(pcid)
    if hit is not None and hit[0] == key:
        return hit[1]
    if key[0] == "html":
        assert html is not None and mtime is not None
        index = await asyncio.to_thread(text_index, html, mtime)
        words = await asyncio.to_thread(count_words, index.text)
    else:
        async with conn.execute(
            "SELECT text FROM chunks WHERE paper_content_id = ? ORDER BY id",
            (pcid,),
        ) as cur:
            texts = [str(r[0] or "") for r in await cur.fetchall()]
        words = await asyncio.to_thread(count_words, "\n".join(texts))
    _cache[pcid] = (key, words)
    return words


async def settle_pieces(conn: aiosqlite.Connection, pcid: int,
                        pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pieces read off a PDF page carry ``raw`` (the text layer's lines):
    their quote is made from it here, its line-end hyphens decided by the
    paper's words. ``raw`` itself is not kept."""
    if not any(isinstance(p.get("raw"), str) for p in pieces):
        return pieces
    words = await paper_words(conn, pcid)
    out: list[dict[str, Any]] = []
    for p in pieces:
        raw = p.get("raw")
        q = {k: v for k, v in p.items() if k != "raw"}
        if isinstance(raw, str) and raw.strip():
            q["quote"] = dehyphen(raw, words)
        out.append(q)
    return out
