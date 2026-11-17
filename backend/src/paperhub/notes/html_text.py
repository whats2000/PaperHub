"""The reading text of a paper rendered as HTML, as the reader sees it.

A mark made in the HTML reader stores its quote in this text, so re-anchoring
searches the same text, never the LaTeX in ``chunks.text``. The rule mirrors
the reader's text index (``frontend/src/lib/marks/anchor.ts``) exactly; both
sides test against ``frontend/src/lib/__fixtures__/mark_text/``:

* the text of ``<body>`` in document order; ``script``, ``style``,
  ``noscript``, ``template``, ``.ph-tr`` and ``[data-ph-overlay]`` are left out;
* a math element (``.math``, ``mjx-container``, ``math``) reads as ``$TeX$``
  (its source, delimiters ``\\(…\\)`` / ``\\[…\\]`` removed, whitespace collapsed);
* whitespace runs collapse to one space, with no leading space;
* moving to another block element reads as one space;
* ``<span id="phchunk-N">`` records where chunk N starts in the text.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from html.parser import HTMLParser

# JavaScript's \s, spelled out so both sides collapse the same characters.
WS = "[ \t\n\r\f\v   -     　﻿]"
_WS_ONE = re.compile(WS)
_WS_RUN = re.compile(WS + "+")

# The reader reads <body> only: <head> (and a <title> outside the body,
# which a browser puts in the head) is not reading text; an svg <title>
# inside the body is, as in the reader.
_SKIP_TAGS = {"script", "style", "noscript", "template", "head"}
# A start tag that closes an open <p> (HTML's implied end tags; the
# browser does this, Python's HTMLParser does not).
_CLOSES_P = {
    "address", "article", "aside", "blockquote", "center", "details", "dialog",
    "dir", "div", "dl", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "main",
    "menu", "nav", "ol", "p", "pre", "search", "section", "summary", "table",
    "ul", "xmp", "listing",
}
_LIST = frozenset({"ul", "ol"})
_DLIST = frozenset({"dl"})
# Elements a <p> / <li> / <dd> search does not look past (button scope).
_SCOPE = {"table", "td", "th", "caption", "html", "button", "object",
          "marquee", "applet", "template", "svg", "math"}
_SKIP_CLASSES = {"ph-tr"}
_BLOCK_TAGS = {
    "p", "li", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th",
    "dt", "dd", "pre", "blockquote", "caption", "div", "section", "article",
    "header", "footer", "figure", "table", "ul", "ol", "body",
}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_DELIMS = (("\\(", "\\)"), ("\\[", "\\]"), ("$$", "$$"), ("$", "$"))
CHUNK_PREFIX = "phchunk-"


def normalise(text: str) -> str:
    return _WS_RUN.sub(" ", text).strip()


def tex_text(source: str) -> str:
    """``$TeX$`` for a math element's source ("" when it has none)."""
    tex = normalise(source)
    for a, b in _DELIMS:
        if tex.startswith(a) and tex.endswith(b) and len(tex) >= len(a) + len(b):
            tex = normalise(tex[len(a):len(tex) - len(b)])
            break
    return f"${tex}$" if tex else ""


@dataclass
class TextIndex:
    text: str = ""
    #: (dom id, index in ``text`` of the next character)
    spans: list[tuple[str, int]] = field(default_factory=list)
    #: (index in ``text`` where a run of one block element starts, that
    #: element's id): which paragraph a character sits in
    paragraphs: list[tuple[int, int]] = field(default_factory=list)


def paragraph_at(index: TextIndex, at: int) -> int | None:
    """The id of the block element (paragraph) the character at ``at`` is
    in. None outside the text, and for loose text directly under <body>
    (the implicit body, id -1), which is no one paragraph."""
    if not 0 <= at < len(index.text) or not index.paragraphs:
        return None
    i = bisect_right(index.paragraphs, at, key=lambda p: p[0]) - 1
    block = index.paragraphs[i][1] if i >= 0 else -1
    return None if block == -1 else block


@dataclass
class _El:
    tag: str
    skip: bool
    math: bool
    block: bool
    uid: int = 0


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_El] = []
        self.chars: list[str] = []
        self.spans: list[tuple[str, int]] = []
        self.paragraphs: list[tuple[int, int]] = []
        self.prev_space = True
        self.prev_block: int | None = None
        self.uids = 0
        self.math_depth: int | None = None  # stack depth of the open atom
        self.math_source: list[str] = []
        self.annotation: list[str] | None = None
        self.math_annotation: str | None = None

    # -- helpers --------------------------------------------------------
    def _skipped(self) -> bool:
        return any(e.skip for e in self.stack)

    def _block_id(self) -> int:
        """The nearest block element's id (-1: the implicit body)."""
        for e in reversed(self.stack):
            if e.block:
                return e.uid if e.tag != "body" else -1
        return -1

    def _enter_block(self, block: int) -> None:
        if self.prev_block is not None and block != self.prev_block and not self.prev_space:
            self.chars.append(" ")
            self.prev_space = True
        if block != self.prev_block:
            self.paragraphs.append((len(self.chars), block))
        self.prev_block = block

    def _emit(self, data: str) -> None:
        for c in data:
            if _WS_ONE.fullmatch(c):
                if self.prev_space:
                    continue
                self.chars.append(" ")
                self.prev_space = True
            else:
                self.chars.append(c)
                self.prev_space = False

    # -- parser events ----------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self.math_depth is not None:
            if tag == "annotation" and (a.get("encoding") or "") == "application/x-tex":
                self.annotation = []
            if tag not in _VOID:
                self.stack.append(_El(tag, False, False, False))
            return
        self._implied_end(tag)
        classes = set((a.get("class") or "").split())
        outside_body = not any(e.tag == "body" for e in self.stack)
        skip = (tag in _SKIP_TAGS or (tag == "title" and outside_body)
                or bool(classes & _SKIP_CLASSES) or "data-ph-overlay" in a)
        math = not skip and ("math" in classes or tag in ("mjx-container", "math"))
        self.uids += 1
        el = _El(tag, skip, math, tag in _BLOCK_TAGS, self.uids)
        dom_id = a.get("id") or ""
        if not skip and not self._skipped() and dom_id.startswith(CHUNK_PREFIX):
            self.spans.append((dom_id, len(self.chars)))
        if tag in _VOID:
            return
        self.stack.append(el)
        if math and not self._skipped():
            self.math_depth = len(self.stack)
            self.math_source = []
            self.math_annotation = None

    def _open_in_scope(self, tags: set[str],
                       bounds: frozenset[str] = frozenset()) -> str | None:
        """The innermost open element named in ``tags`` that a start tag
        may close (none past a scope boundary or one of ``bounds``)."""
        for e in reversed(self.stack):
            if e.tag in tags:
                return e.tag
            if e.tag in _SCOPE or e.tag in bounds:
                return None
        return None

    def _implied_end(self, tag: str) -> None:
        """Close what the browser closes before ``tag`` opens."""
        if tag in _CLOSES_P and self._open_in_scope({"p"}):
            self.handle_endtag("p")
        if tag == "li" and (open_ := self._open_in_scope({"li"}, _LIST)):
            self.handle_endtag(open_)
        if tag in ("dd", "dt") and (
                open_ := self._open_in_scope({"dd", "dt"}, _DLIST)):
            self.handle_endtag(open_)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        # Close up to the matching open tag (tolerates unclosed children).
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                break
        else:
            return
        while len(self.stack) > i:
            if tag == "annotation" and self.annotation is not None and len(self.stack) == i + 1:
                self.math_annotation = "".join(self.annotation)
                self.annotation = None
            if self.math_depth is not None and len(self.stack) == self.math_depth:
                self._close_math()
            self.stack.pop()

    def _close_math(self) -> None:
        source = self.math_annotation if self.math_annotation is not None else "".join(self.math_source)
        self.math_depth = None
        text = tex_text(source)
        if not text:
            return
        self._enter_block(self._block_id())
        self.chars.extend(text)
        self.prev_space = False

    def handle_data(self, data: str) -> None:
        if self.math_depth is not None:
            self.math_source.append(data)
            if self.annotation is not None:
                self.annotation.append(data)
            return
        if self._skipped() or not data:
            return
        self._enter_block(self._block_id())
        self._emit(data)


def build_text_index(html: str) -> TextIndex:
    b = _Builder()
    b.feed(html)
    b.close()
    if b.math_depth is not None:
        b._close_math()
    return TextIndex(text="".join(b.chars), spans=b.spans, paragraphs=b.paragraphs)
