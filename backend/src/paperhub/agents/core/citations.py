"""Citation validation for the core agent: an answer may only cite chunks the
agent actually read (this turn, or in an earlier turn it still holds)."""
from __future__ import annotations

import re

# [chunk:12], [chunk:12, 13], or the [chunk:12, chunk:13] form models also write
_MARKER = re.compile(
    r"[ \t]?\[chunk:\s*(\d+(?:\s*,\s*(?:chunk:\s*)?\d+)*)\s*\]")


# A citation that fails validation becomes a visible mark instead of
# vanishing: the reader sees that this claim's source could not be checked.
# Reasons: not_read, figure_not_viewed, region_not_viewed, quote_not_found.
UNSOURCED_RE = re.compile(r"\[unsourced:\s*([a-z_]+)\s*\]")


def unsourced(reason: str, lead: str = "") -> str:
    return f"{lead}[unsourced:{reason}]"


def _lead(m: re.Match[str]) -> str:
    return m.group(0)[0] if m.group(0)[0] in " \t" else ""


def _ids(group: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", group)]


def cited_ids(text: str) -> list[int]:
    """Every chunk id cited in ``text``, first-seen order, unique."""
    out: list[int] = []
    for m in _MARKER.finditer(text):
        for i in _ids(m.group(1)):
            if i not in out:
                out.append(i)
    return out


def validate_citations(text: str, allowed_ids: set[int]) -> tuple[str, list[int]]:
    """Drop ``[chunk:N]`` ids (members of ``[chunk:N,M]``) not in
    ``allowed_ids``; a marker left with no id becomes ``[unsourced:not_read]``.
    Returns the text and the removed ids (first-seen order, unique)."""
    removed: list[int] = []

    def fix(m: re.Match[str]) -> str:
        ids = _ids(m.group(1))
        keep = [i for i in ids if i in allowed_ids]
        for i in ids:
            if i not in allowed_ids and i not in removed:
                removed.append(i)
        if len(keep) == len(ids) and "chunk:" not in m.group(1):
            return m.group(0)
        if not keep:
            return unsourced("not_read", _lead(m))
        return f"{_lead(m)}[chunk:{','.join(map(str, keep))}]"

    return _MARKER.sub(fix, text), removed


# [fig:20/fig-003], [fig:20/fig-003, 20/fig-004] or [fig:20/fig-003, fig:20/fig-004]:
# a figure image the agent looked at (view_figure), as "<paper>/<figure id>".
_FIG_KEY = r"\d+\s*/\s*[A-Za-z0-9_.-]+"
_FIG_MARKER = re.compile(
    rf"[ \t]?\[fig:\s*({_FIG_KEY}(?:\s*,\s*(?:fig:\s*)?{_FIG_KEY})*)\s*\]")


def figure_key(paper: int, figure_id: str) -> str:
    return f"{paper}/{figure_id}"


def _fig_keys(group: str) -> list[str]:
    return [re.sub(r"\s+", "", k) for k in re.findall(_FIG_KEY, group)]


def cited_figures(text: str) -> list[str]:
    """Every figure cited in ``text`` as ``"<paper>/<figure id>"``,
    first-seen order, unique."""
    out: list[str] = []
    for m in _FIG_MARKER.finditer(text):
        for k in _fig_keys(m.group(1)):
            if k not in out:
                out.append(k)
    return out


def validate_figure_citations(
    text: str, allowed: set[str],
) -> tuple[str, list[str]]:
    """Drop ``[fig:P/ID]`` keys naming a figure the agent did not look at; a
    marker left with none becomes ``[unsourced:figure_not_viewed]``. Returns
    the text and the removed keys."""
    removed: list[str] = []

    def fix(m: re.Match[str]) -> str:
        keys = _fig_keys(m.group(1))
        keep = [k for k in keys if k in allowed]
        for k in keys:
            if k not in allowed and k not in removed:
                removed.append(k)
        if not keep:
            return unsourced("figure_not_viewed", _lead(m))
        lead = _lead(m)
        if len(keep) == len(keys):
            return m.group(0)
        return f"{lead}[fig:{', '.join(keep)}]"

    return _FIG_MARKER.sub(fix, text), removed


# [view:20/5/120,340,560,720]: a page region the agent viewed (page tools),
# as "<paper>/<page>/<x0>,<y0>,<x1>,<y1>" — 1-based page, 0-1000 box.
_VIEW_MARKER = re.compile(
    r"[ \t]?\[view:\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]")


def view_key(paper: int, page: int, box: tuple[int, int, int, int]) -> str:
    return f"{paper}/{page}/{','.join(str(v) for v in box)}"


def _view_key_of(m: re.Match[str]) -> str:
    g = [int(x) for x in m.groups()]
    return view_key(g[0], g[1], (g[2], g[3], g[4], g[5]))


def cited_views(text: str) -> list[str]:
    """Every page region cited in ``text``, first-seen order, unique."""
    out: list[str] = []
    for m in _VIEW_MARKER.finditer(text):
        k = _view_key_of(m)
        if k not in out:
            out.append(k)
    return out


def _parse_view_key(key: str) -> tuple[int, int, tuple[int, int, int, int]]:
    paper, page, box = key.split("/", 2)
    x0, y0, x1, y1 = (int(v) for v in box.split(","))
    return int(paper), int(page), (x0, y0, x1, y1)


def _enclosing_view(key: str, allowed: set[str]) -> str | None:
    """The smallest viewed region on the same page that holds ``key``'s box."""
    paper, page, (x0, y0, x1, y1) = _parse_view_key(key)
    best: tuple[int, str] | None = None
    for a in allowed:
        ap, apage, (ax0, ay0, ax1, ay1) = _parse_view_key(a)
        if (ap, apage) == (paper, page) and ax0 <= x0 and ay0 <= y0 and x1 <= ax1 and y1 <= ay1:
            area = (ax1 - ax0) * (ay1 - ay0)
            if best is None or area < best[0]:
                best = (area, a)
    return best[1] if best else None


def validate_view_citations(
    text: str, allowed: set[str],
) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Keep ``[view:]`` markers of regions the agent viewed, exactly as a tool
    returned them. A box the model wrote itself that lies inside a viewed
    region cites what it saw: it becomes the smallest such region. Any other
    marker becomes ``[unsourced:region_not_viewed]``. Returns the text, the removed keys and the
    (written, snapped-to) pairs."""
    removed: list[str] = []
    snapped: list[tuple[str, str]] = []

    def fix(m: re.Match[str]) -> str:
        k = _view_key_of(m)
        if k in allowed:
            return m.group(0)
        target = _enclosing_view(k, allowed)
        if target is not None:
            if (k, target) not in snapped:
                snapped.append((k, target))
            lead = m.group(0)[: len(m.group(0)) - len(m.group(0).lstrip(" \t"))]
            return f"{lead}[view:{target}]"
        if k not in removed:
            removed.append(k)
        return unsourced("region_not_viewed", _lead(m))

    return _VIEW_MARKER.sub(fix, text), removed, snapped


# A quotation followed by its [chunk:] citation. Straight or curly double
# quotes; CJK quotes are skipped (often a translation, never verbatim).
_QUOTE = re.compile(r"[\"“]([^\"”\n]{12,600})[\"”]")
_SENTENCE_END = re.compile(r"[.!?。！？](?:\s|$)")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
QUOTE_MIN_WORDS = 4
QUOTE_LOOKAHEAD = 120
QUOTE_GAP = 80              # tokens a quote may skip once (PDF page artifacts)
_QUOTE_GAPS = re.compile(r"…|\.\.\.|\$[^$]*\$")


def _quote_tokens(text: str) -> list[str]:
    """Lower-case alphanumeric runs after NFKC (ligatures, curly quotes), with
    a word broken by a line-end hyphen joined — PDF text and the model's
    retyping then compare word for word."""
    import unicodedata

    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.findall(r"[0-9a-z]+", text)


def _find(hay: list[str], toks: list[str], pos: int) -> int:
    """End index of ``toks`` in ``hay`` from ``pos``, allowing one skip of up
    to ``QUOTE_GAP`` tokens between two of them (a page number, running head
    or footnote the PDF text puts inside a sentence); -1 when absent."""
    n = len(toks)
    for i in range(pos, len(hay) - n + 1):
        if hay[i:i + n] == toks:
            return i + n
    for k in range(1, n):
        head, tail = toks[:k], toks[k:]
        for i in range(pos, len(hay) - k + 1):
            if hay[i:i + k] != head:
                continue
            for j in range(i + k + 1, min(len(hay) - len(tail), i + k + QUOTE_GAP) + 1):
                if hay[j:j + len(tail)] == tail:
                    return j + len(tail)
    return -1


def _quote_in(quote: str, text: str) -> bool:
    """Every part of ``quote`` between ellipses and ``$…$`` math (the PDF text
    holds math as flattened symbols) occurs, in order, in ``text``."""
    hay = _quote_tokens(text)
    pos = 0
    for part in _QUOTE_GAPS.split(quote):
        toks = _quote_tokens(part)
        if not toks:
            continue
        pos = _find(hay, toks, pos)
        if pos < 0:
            return False
    return True


def _sourced_by(quote: str, i: int, chunk_texts: dict[int, str]) -> bool:
    """Chunk ``i`` holds ``quote``, alone or as the start / end of a quote that
    runs on into a neighbouring chunk the agent also read."""
    own = chunk_texts.get(i, "")
    if _quote_in(quote, own):
        return True
    for a, b in ((i - 1, i), (i, i + 1)):
        other = b if a == i else a
        if other in chunk_texts and not _quote_in(quote, chunk_texts[other]) and \
                _quote_in(quote, chunk_texts.get(a, "") + "\n" + chunk_texts.get(b, "")):
            return True
    return False


def verify_quotes(
    text: str, chunk_texts: dict[int, str],
) -> tuple[str, list[dict[str, object]]]:
    """A quotation cited as ``[chunk:N]`` must be in chunk N. ``chunk_texts``
    holds the chunks the agent read (the allowed ids). When another read chunk
    holds the words, the citation moves there; when none does, it becomes
    ``[unsourced:quote_not_found]`` so no quotation claims a source that lacks
    it. Short (< 4 words) and CJK quotations are not checked. Returns the text
    and one fix per changed citation: ``{quote, cited, action: moved|marked,
    to?}``."""
    fixes: list[dict[str, object]] = []
    edits: dict[tuple[int, int], str] = {}

    def check(quote: str, m: re.Match[str], at: int) -> None:
        quote = quote.strip()
        if _CJK.search(quote) or len(re.findall(r"[A-Za-z]+", quote)) < QUOTE_MIN_WORDS:
            return
        span = (at + m.start(), at + m.end())
        if span in edits:
            return
        cited = _ids(m.group(1))
        if any(_sourced_by(quote, i, chunk_texts) for i in cited):
            return
        holders = [i for i in chunk_texts if _sourced_by(quote, i, chunk_texts)]
        lead = m.group(0)[0] if m.group(0)[0] in " \t" else ""
        if holders:
            edits[span] = f"{lead}[chunk:{holders[0]}]"
            fixes.append({"quote": quote, "cited": cited, "action": "moved",
                          "to": [holders[0]]})
        else:
            edits[span] = unsourced("quote_not_found", lead)
            fixes.append({"quote": quote, "cited": cited, "action": "marked"})

    for q in _QUOTE.finditer(text):
        raw, base = q.group(1), q.start(1)
        # Citations inside the quotation source the words before them (a
        # quote spanning chunks is cited sentence by sentence).
        prev = 0
        for m in _MARKER.finditer(raw):
            check(raw[prev:m.start()], m, base)
            prev = m.end()
        # The rest is sourced by the citation right after the quotation, in
        # the same sentence and paragraph.
        window = text[q.end():q.end() + QUOTE_LOOKAHEAD].split("\n", 1)[0]
        end = _SENTENCE_END.search(window)
        window = window[:end.start() + 1] if end else window
        after = _MARKER.search(window)
        if after is not None:
            check(raw[prev:], after, q.end())
    for (start, stop), new in sorted(edits.items(), reverse=True):
        text = text[:start] + new + text[stop:]
    return text, fixes
