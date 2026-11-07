"""Narrow an idea's citation to the chunks that carry its mechanism (QA I5).

A card cites whole sections, and a section can be large: AlphaGenome's
"Methods" is 130 chunks, of which ~9 describe the U-Net the idea borrows, so a
reader sent to the whole section lands in data-acquisition text. ``narrow``
scores each chunk of the cited section(s) against the idea's own words (the
card field text that cites the section, the idea's title and rationale) with
BM25 (IDF over the WHOLE document, so terms that are everywhere in the paper
count for little) and keeps the best contiguous window of at most ``WINDOW``
chunks. Deterministic, no LLM call.

When nothing scores (no shared term, or the best window matches fewer than
``MIN_TERMS`` distinct query terms), the whole section is kept: an honest
coarse citation beats a confident wrong one. A section no larger than the
window is kept whole.

Several candidate sections (a copied ``"A; B"`` cites list): the stored
section is the one holding the best window.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import aiosqlite

from paperhub.agents.sl_read import read_section_chunks

WINDOW = 4
"""Most chunks a narrowed citation keeps (contiguous within one section)."""
MIN_TERMS = 2
"""Distinct query terms the best window must contain to count as a match."""
_K1, _B = 1.2, 0.75

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")
_STOP = frozenset("""
a an and are as at be been but by can each for from has have in into is it its
of on or our over that the their them then there these this those through to
under up use used uses using via was we were what when where which while who
will with within without would not no than so such also more most only other
one two all any both every same new idea ideas let make makes made apply
applies applied problem problems mechanism method methods approach paper
""".split())  # noqa: SIM905 — a word list reads better as text


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens without stop words, light plural folding."""
    out: list[str] = []
    for raw in _WORD.findall(text):
        w = raw.casefold().strip("-")
        if len(w) < 3 or w in _STOP:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


@dataclass(frozen=True)
class Narrowed:
    section: str
    chunk_ids: list[int]
    narrowed: bool
    """False = the whole section was kept (small, or nothing scored)."""
    score: float = 0.0


def best_window(
    query: list[str], chunks: list[list[str]], doc_freq: Counter[str], n_docs: int,
    avg_len: float, window: int = WINDOW,
) -> tuple[int, int, float, int] | None:
    """``(start, end, score, distinct_terms)`` of the best contiguous window of
    at most ``window`` chunks (end exclusive), or None when nothing scores.
    Windows are compared by ``score / sqrt(size)``: one strong chunk beats four
    weak ones, but a run of strong chunks is kept together."""
    q = set(query)
    if not q or not chunks:
        return None
    scores: list[float] = []
    hits: list[set[str]] = []
    for toks in chunks:
        tf = Counter(toks)
        s = 0.0
        for term in q:
            f = tf.get(term, 0)
            if not f:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            s += idf * f * (_K1 + 1) / (f + _K1 * (1 - _B + _B * len(toks) / (avg_len or 1)))
        scores.append(s)
        hits.append(q & tf.keys())
    best: tuple[int, int, float, int] | None = None
    best_key = 0.0
    for size in range(1, min(window, len(chunks)) + 1):
        for start in range(0, len(chunks) - size + 1):
            end = start + size
            if scores[start] <= 0 or scores[end - 1] <= 0:
                continue  # a window never starts or ends on a chunk that scores 0
            total = sum(scores[start:end])
            key = total / math.sqrt(size)
            if best is None or key > best_key:
                terms: set[str] = set()
                for h in hits[start:end]:
                    terms |= h
                best, best_key = (start, end, total, len(terms)), key
    return best


async def _doc_stats(
    conn: aiosqlite.Connection, paper_content_id: int,
) -> tuple[Counter[str], int, float]:
    async with conn.execute(
        "SELECT text FROM chunks WHERE paper_content_id = ?", (paper_content_id,),
    ) as cur:
        all_text = [tokens(str(r[0])) for r in await cur.fetchall()]
    doc_freq: Counter[str] = Counter()
    for toks in all_text:
        doc_freq.update(set(toks))
    n = len(all_text) or 1
    avg = sum(len(t) for t in all_text) / n if all_text else 1.0
    return doc_freq, n, avg


async def narrow(
    conn: aiosqlite.Connection, *, paper_content_id: int, sections: list[str],
    query_text: str, window: int = WINDOW,
) -> Narrowed | None:
    """The best-scoring window across ``sections``; when nothing scores, the
    first section with chunks, kept whole; None when no section has chunks."""
    q = tokens(query_text)
    doc_freq, n_docs, avg_len = await _doc_stats(conn, paper_content_id)
    whole: Narrowed | None = None
    best: Narrowed | None = None
    for name in sections:
        ids = (await read_section_chunks(
            paper_content_id=paper_content_id, section_name=name, conn=conn)).chunk_ids
        if not ids:
            continue
        if whole is None:
            whole = Narrowed(section=name, chunk_ids=ids, narrowed=False)
        marks = ",".join("?" * len(ids))
        async with conn.execute(
            f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids,
        ) as cur:
            text_by_id = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
        hit = best_window(q, [tokens(text_by_id.get(i, "")) for i in ids],
                          doc_freq, n_docs, avg_len, window)
        if hit is None or hit[3] < MIN_TERMS:
            continue
        start, end, score, _terms = hit
        if best is None or score > best.score:
            keep = ids if len(ids) <= window else ids[start:end]
            best = Narrowed(section=name, chunk_ids=keep,
                            narrowed=len(keep) < len(ids), score=score)
    return best or whole
