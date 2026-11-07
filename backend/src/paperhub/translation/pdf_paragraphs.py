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
"""

from __future__ import annotations

import html as html_lib
import re

from paperhub.pipelines.paper_asset import BlockAsset
from paperhub.translation.models import PdfFragment, PdfParagraph

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


def build_paragraphs(blocks: list[BlockAsset]) -> list[PdfParagraph]:
    paras: list[PdfParagraph] = []
    cur: PdfParagraph | None = None
    open_para = False  # cur ended mid-sentence and may absorb the next Text block
    for b in blocks:
        if b.block_type not in TRANSLATABLE:
            if b.block_type not in _BRIDGE:
                open_para = False  # hard break
            continue
        text, items, trailing = _convert(b.html)
        if not text:
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
        if cur is not None and open_para and mergeable:
            cur.text = f"{cur.text} ⟦BR⟧ {_shift(text, len(cur.math))}"
            cur.math.extend(items)
            if frag:
                cur.fragments.append(frag)
        else:
            cur = PdfParagraph(key=f"p:{b.block_id}", text=text, math=items,
                               fragments=[frag] if frag else [])
            paras.append(cur)
        open_para = mergeable and not _ends_sentence(text, trailing)
    return paras
