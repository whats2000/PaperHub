"""An inline citation of a mark in note text (H5b.1, [user, 2026-11-21]).

A new text mark whose quote is short lands in the note as a ``markCite``
chip instead of a reference block: the same mark in another form. Offsets
are the editor's (ProseMirror) positions inside a block's text: a text
character counts its UTF-16 code units, any inline node (a chip, inline
math, a mention) counts one.
"""
from __future__ import annotations

import unicodedata
from typing import Any

# The longest quote, in user-perceived characters after trimming, that
# lands as an inline citation. The frontend's ``INLINE_CITE_MAX_GRAPHEMES``
# (markCiteText.ts) holds the same value.
INLINE_CITE_MAX_GRAPHEMES = 24

# Blocks whose text can hold an inline citation (the editor's list).
TEXT_BLOCK_TYPES = frozenset({
    "paragraph", "heading", "bulletListItem", "numberedListItem",
    "checkListItem", "quote", "callout"})

_ZWJ = 0x200D


def _extends(cp: int) -> bool:
    """A code point that joins the grapheme before it."""
    if unicodedata.category(chr(cp)) in ("Mn", "Me", "Mc"):
        return True
    return (cp == _ZWJ or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF
            or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F
            or 0x1160 <= cp <= 0x11FF)  # emoji modifiers, tags, jamo V/T


def _regional(cp: int) -> bool:
    return 0x1F1E6 <= cp <= 0x1F1FF


def grapheme_len(text: str) -> int:
    """User-perceived characters (grapheme clusters, close to UAX #29, as
    the browser's ``Intl.Segmenter`` counts them)."""
    count = 0
    prev: int | None = None
    ri_open = False  # a regional indicator waiting for its pair
    for ch in text:
        cp = ord(ch)
        if prev is None:
            count += 1
            ri_open = _regional(cp)
        elif _extends(cp) or prev == _ZWJ:
            pass
        elif _regional(cp) and ri_open:
            ri_open = False
        else:
            count += 1
            ri_open = _regional(cp)
        prev = cp
    return count


def is_short_quote(quote: str) -> bool:
    """Whether a text mark's quote lands as an inline citation."""
    text = quote.strip()
    return bool(text) and grapheme_len(text) <= INLINE_CITE_MAX_GRAPHEMES


def cite_run(mark_id: str, paper_id: int) -> dict[str, Any]:
    """A new inline citation: the default text (the quote's start)."""
    return {"type": "markCite",
            "props": {"markId": mark_id, "text": "", "paperId": paper_id}}


def _u16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _run_size(run: dict[str, Any]) -> int:
    if run.get("type") == "text":
        return _u16(str(run.get("text", "")))
    if run.get("type") == "link" and isinstance(run.get("content"), list):
        return sum(_run_size(r) for r in run["content"]
                   if isinstance(r, dict))
    return 1


def _split_at(text: str, units: int) -> int:
    """The string index at ``units`` UTF-16 code units (never inside a
    surrogate pair: then just after it)."""
    seen = 0
    for i, ch in enumerate(text):
        if seen >= units:
            return i
        seen += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def content_size(content: list[dict[str, Any]]) -> int:
    return sum(_run_size(r) for r in content if isinstance(r, dict))


def insert_cite(content: list[dict[str, Any]] | None, offset: int | None,
                run: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """``content`` with ``run`` at ``offset`` (None or past the end: the
    end), and the offset just after it. Inside a link it goes after the
    link."""
    runs = [r for r in (content or []) if isinstance(r, dict)]
    size = content_size(runs)
    at = size if offset is None else max(0, min(offset, size))
    out: list[dict[str, Any]] = []
    pos = 0
    placed: int | None = None
    for r in runs:
        n = _run_size(r)
        if placed is None and pos <= at < pos + n and at > pos:
            if r.get("type") == "text":
                text = str(r.get("text", ""))
                cut = _split_at(text, at - pos)
                out.append({**r, "text": text[:cut]})
                out.append(run)
                placed = pos + _u16(text[:cut]) + 1
                out.append({**r, "text": text[cut:]})
                pos += n
                continue
            out.append(r)
            out.append(run)
            placed = pos + n + 1
            pos += n
            continue
        if placed is None and at == pos:
            out.append(run)
            placed = pos + 1
        out.append(r)
        pos += n
    if placed is None:
        out.append(run)
        placed = pos + 1
    return out, placed


def remove_cite(content: list[dict[str, Any]] | None, mark_id: str,
                ) -> tuple[list[dict[str, Any]], int | None]:
    """``content`` without the inline citations of ``mark_id``, and where
    the first one was (None: there was none)."""
    out: list[dict[str, Any]] = []
    pos = 0
    at: int | None = None
    for r in content or []:
        if not isinstance(r, dict):
            continue
        if (r.get("type") == "markCite"
                and (r.get("props") or {}).get("markId") == mark_id):
            if at is None:
                at = pos
            continue
        out.append(r)
        pos += _run_size(r)
    return out, at


def has_cite(content: Any, mark_id: str) -> bool:
    """Whether a block's runs hold an inline citation of ``mark_id``."""
    if not isinstance(content, list):
        return False
    return any(isinstance(r, dict) and r.get("type") == "markCite"
               and (r.get("props") or {}).get("markId") == mark_id
               for r in content)
