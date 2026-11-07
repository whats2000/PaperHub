"""Build translatable PDF paragraphs from persisted Marker blocks (Plan H1).

Marker splits prose at column and page breaks and around display equations, so
a ``Text``/``TextInlineMath`` block that does not end a sentence is merged with
the next one. Continuation bridges only ``Equation``/``PageHeader``/``PageFooter``
blocks; every other skipped type (headings, tables, figures) is a hard break,
and ``Caption``/``Footnote``/``ListItem`` never merge. A ``Text`` block that
starts with a reference marker is a footnote Marker mistyped (e.g. an
affiliation line): it is its own paragraph and bridges an open one. Inline math and
reference markers become ``⟦n⟧`` placeholders; ``math[n]`` is always LaTeX
(references are wrapped in ``\text{...}``). A merge boundary is ``⟦BR⟧``.

A merge also needs the layout to say "continued": the next block is on
another page, in another column (it starts above or right of the open one),
or past a bridge. Two blocks stacked in one column with nothing between them
are two paragraphs; this keeps front matter (bylines, affiliations, dates,
abstract), whose lines never end a sentence, from fusing into one unit.

Not translated at all (no paragraph, so no hover card): the reference list
(blocks under a References / Bibliography heading or section, up to the next
heading) and name, affiliation, correspondence, date and DOI lines
(``quality.is_meta_block``). A skipped meta line bridges like a footnote.
"""

from __future__ import annotations

import html as html_lib
import re

from paperhub.pipelines.paper_asset import BlockAsset
from paperhub.translation.models import PdfFragment, PdfParagraph
from paperhub.translation.quality import is_meta_block

TRANSLATABLE = frozenset({"Text", "ListItem", "Caption", "Footnote", "TextInlineMath"})
_MERGEABLE = frozenset({"Text", "TextInlineMath"})
_BRIDGE = frozenset({"Equation", "PageHeader", "PageFooter"})
_SENTENCE_END = ".?!。？！:;："
_CLOSERS = ")]\"”’'」』）】"
_TOKEN = re.compile(
    r"<math\b[^>]*>(?P<math>.*?)</math>"
    r"|<(?P<tag>a|sup)\b[^>]*>(?P<ref>.*?)</(?P=tag)>",
    re.S | re.I,
)
_BREAKS = re.compile(r"<br\s*/?>|</?(?:p|div|li|ul|ol|h[1-6]|tr|td|th)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_REFS_HEADING = re.compile(
    r"^\W*(?:\d+\.?\s*)?(?:supplementary\s+)?"
    r"(?:references|bibliography|works\s+cited|literature\s+cited|參考文獻|参考文献)\W*$",
    re.I,
)
# Points a block may start above the open one's bottom and still be "below" it.
_STACK_SLOP = 2.0
_LATEX_TEXT_ESC = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#",
    "&": r"\&", "_": r"\_", "^": r"\^{}", "~": r"\~{}", "$": r"\$",
}


def _plain(s: str) -> str:
    return _WS.sub(" ", html_lib.unescape(_TAG.sub("", s))).strip()


def _latex_text(s: str) -> str:
    return r"\text{" + "".join(_LATEX_TEXT_ESC.get(c, c) for c in s) + "}"


def _prose(html: str) -> str:
    """Strip tags from a prose fragment; breaks become spaces so words don't glue."""
    return _TAG.sub("", _BREAKS.sub(" ", html))


def _convert(html: str) -> tuple[str, list[str], str]:
    """Return (text with placeholders from 0, protected items, trailing kind).

    Trailing kind is 'math' / 'ref' when the text ends with such a placeholder."""
    items: list[str] = []
    kinds: list[str] = []
    out: list[str] = []
    pos = 0
    for m in _TOKEN.finditer(html):
        out.append(_prose(html[pos:m.start()]))
        if m.group("math") is not None:
            items.append(_plain(m.group("math")))
            kinds.append("math")
        else:
            items.append(_latex_text(_plain(m.group("ref"))))
            kinds.append("ref")
        out.append(f"⟦{len(items) - 1}⟧")
        pos = m.end()
    out.append(_prose(html[pos:]))
    text = _WS.sub(" ", html_lib.unescape("".join(out))).strip()
    trailing = ""
    if kinds and re.search(rf"⟦{len(kinds) - 1}⟧$", text):
        trailing = kinds[-1]
    return text, items, trailing


def _ends_sentence(text: str, trailing: str) -> bool:
    if trailing == "math":
        return False  # inline math at the end: the sentence runs on
    t = text.rstrip()
    if trailing == "ref":  # "...model.[1]" -> judge by what precedes the marker
        t = re.sub(r"(?:⟦\d+⟧)+$", "", t).rstrip()
    t = t.rstrip(_CLOSERS).rstrip()
    return bool(t) and t[-1] in _SENTENCE_END


def _shift(text: str, offset: int) -> str:
    return re.sub(r"⟦(\d+)⟧", lambda m: f"⟦{int(m.group(1)) + offset}⟧", text)


def _footnote_like(text: str, items: list[str]) -> bool:
    r"""Starts with a reference-marker placeholder (``⟦0⟧`` = ``\text{...}``)."""
    return text.startswith("⟦0⟧") and bool(items) and items[0].startswith(r"\text{")


def is_references_heading(title: str) -> bool:
    return bool(_REFS_HEADING.match(title.strip()))


def _continues(last: PdfFragment | None, nxt: PdfFragment | None) -> bool:
    """The layout lets ``nxt`` continue the paragraph ending at ``last``: another
    page, or another column (it starts above or right of ``last``). Without
    geometry on either side, merging is decided by the text alone."""
    if last is None or nxt is None:
        return True
    if nxt.page != last.page:
        return True
    lx0, _ly0, lx1, ly1 = last.bbox
    nx0, ny0, _nx1, _ny1 = nxt.bbox
    return ny0 < ly1 - _STACK_SLOP or nx0 >= lx1


def build_paragraphs(blocks: list[BlockAsset]) -> list[PdfParagraph]:
    paras: list[PdfParagraph] = []
    cur: PdfParagraph | None = None
    open_para = False  # cur ended mid-sentence and may absorb the next Text block
    bridged = False  # a bridge block (or skipped line) sits after cur's last block
    in_refs = False  # under a References heading, up to the next heading
    for b in blocks:
        if b.block_type == "SectionHeader":
            in_refs = is_references_heading(_plain(b.html))
        if b.block_type not in TRANSLATABLE:
            if b.block_type in _BRIDGE:
                bridged = True
            else:
                open_para = False  # hard break
            continue
        if in_refs or (b.section is not None and is_references_heading(b.section)):
            open_para = False
            continue
        text, items, trailing = _convert(b.html)
        if not text:
            continue
        if b.block_type != "Caption" and is_meta_block(text):
            bridged = True  # like a footnote: the open paragraph may go on past it
            continue
        x0, y0, x1, y1 = b.bbox
        # Task 7 falls back to a zero bbox when Marker gave no geometry.
        frag = PdfFragment(page=b.page, bbox=b.bbox) if x1 > x0 and y1 > y0 else None
        if _footnote_like(text, items):
            # Own paragraph; `cur` and `open_para` stay on the open one.
            paras.append(PdfParagraph(key=f"p:{b.block_id}", text=text, math=items,
                                      fragments=[frag] if frag else []))
            continue
        mergeable = b.block_type in _MERGEABLE
        last = cur.fragments[-1] if cur is not None and cur.fragments else None
        if cur is not None and open_para and mergeable and (bridged or _continues(last, frag)):
            cur.text = f"{cur.text} ⟦BR⟧ {_shift(text, len(cur.math))}"
            cur.math.extend(items)
            if frag:
                cur.fragments.append(frag)
        else:
            cur = PdfParagraph(key=f"p:{b.block_id}", text=text, math=items,
                               fragments=[frag] if frag else [])
            paras.append(cur)
        open_para = mergeable and not _ends_sentence(text, trailing)
        bridged = False
    return paras
