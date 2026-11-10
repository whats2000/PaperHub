"""Page regions for chunks that came without one.

Marker chunks carry the page and box they were read from; PyMuPDF chunks and
early Marker chunks do not, so the agent cannot `view_chunk` them and the
reader falls back to text search. Their text came from the paper's own PDF,
so each chunk is found there by its words: the first words fix the start,
the last words the end, and the region is the box around the chunk's words
on the page where it starts (0-based page, PDF points, top-left origin —
the same convention as Marker's).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

Box = tuple[float, float, float, float]

ANCHOR_WORDS = 6        # words matched at each end of a chunk
START_TRIES = 8         # later start windows tried when the first words miss
_TOKEN = re.compile(r"[0-9a-z]+")


@dataclass(frozen=True)
class _Word:
    token: str
    page: int
    box: Box


def _tokens(text: str) -> list[str]:
    """Lower-case alphanumeric runs after NFKC (ligatures split back out);
    a word broken by a line-end hyphen joins its halves."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"-\s*\n\s*", "", text)
    return _TOKEN.findall(text)


def _document_words(pdf: Path) -> list[_Word]:
    import pymupdf

    words: list[_Word] = []
    with pymupdf.open(pdf) as doc:  # type: ignore[no-untyped-call]
        for pno, page in enumerate(doc):
            raw = page.get_text("words")
            # Join a line-end hyphenated word with the next word's start.
            pending: tuple[str, Box] | None = None
            for x0, y0, x1, y1, w, *_ in raw:
                box = (float(x0), float(y0), float(x1), float(y1))
                if pending is not None:
                    text, pbox = pending
                    pending = None
                    for t in _tokens(text + w):
                        words.append(_Word(t, pno, pbox))
                    continue
                if w.endswith("-") and len(w) > 1:
                    pending = (w[:-1], box)
                    continue
                for t in _tokens(w):
                    words.append(_Word(t, pno, box))
            if pending is not None:
                for t in _tokens(pending[0]):
                    words.append(_Word(t, pno, pending[1]))
    return words


def _find(stream: list[str], needle: list[str], start: int) -> int:
    """Index of ``needle`` in ``stream`` at or after ``start``, else -1."""
    n = len(needle)
    if n == 0:
        return -1
    first = needle[0]
    for i in range(start, len(stream) - n + 1):
        if stream[i] == first and stream[i:i + n] == needle:
            return i
    return -1


def locate_chunk_regions(pdf: Path, chunks: list[tuple[int, str]]) -> dict[int, tuple[int, Box]]:
    """``{chunk id: (0-based page, box)}`` for every chunk found in ``pdf``.
    Chunks are searched in order (a later chunk starts after an earlier one,
    falling back to the whole document); a chunk whose words are not found
    is left out, so the caller keeps text search for it."""
    words = _document_words(pdf)
    stream = [w.token for w in words]
    out: dict[int, tuple[int, Box]] = {}
    cursor = 0
    for cid, text in chunks:
        toks = _tokens(text)
        if len(toks) < 2:
            continue
        k = min(ANCHOR_WORDS, len(toks))
        start = -1
        for off in range(0, min(START_TRIES, max(1, len(toks) - k + 1))):
            window = toks[off:off + k]
            start = _find(stream, window, cursor)
            if start < 0:
                start = _find(stream, window, 0)
            if start >= 0:
                start = max(0, start - off)
                break
        if start < 0:
            continue
        tail = toks[-k:]
        end_at = _find(stream, tail, start)
        end = end_at + k - 1 if end_at >= 0 else min(len(stream) - 1, start + len(toks) - 1)
        page = words[start].page
        boxes = [w.box for w in words[start:end + 1] if w.page == page]
        out[cid] = (page, (min(b[0] for b in boxes), min(b[1] for b in boxes),
                           max(b[2] for b in boxes), max(b[3] for b in boxes)))
        cursor = end + 1
    return out
