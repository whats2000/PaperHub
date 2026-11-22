"""A note as Markdown: the text, its images and a BibTeX file of its sources.

Sections become ``##`` headings; a quote from a paper a block quote with
"— <paper>, §<section>, p. <page> [@key]" under it; figures, equations and
boxes an image file (the figure's own image, else a page crop) with its
caption; math blocks and equations ``$$…$$``; dividers ``---``. Citations
(``[chunk:N]``, ``[fig:P/ID]``, ``[view:P/N/box]``, an ``@`` mention of a
paper) become ``[@key]`` with one ``references.bib`` entry per paper cited.

Comment threads are never note text: a commented range exports as plain
text and the thread stays out. Hidden text exports as plain text.

Note text is untrusted Markdown input. Every user string first passes
``_user_text`` (one normalisation point: line ends unified, parser-steering
and private-use characters dropped). Then everything Markdown gives a
meaning is escaped (``!`` included, so text never forms an image; ``$``,
since the editor has no inline math; a scheme's ``:`` and the ``.`` of
``www.``, so a GFM viewer links nothing on its own), line starts are
escaped against block syntax, link targets are percent-encoded and a code
block's language must be a plain name. Only the exporter's own references
are images, links and headings.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiosqlite

from paperhub.notes.mark_pieces import live_pieces
from paperhub.notes.marks import joined_quote
from paperhub.notes.models import Block, Note, table_aligns, table_rows
from paperhub.notes.run_text import link_text, read_runs
from paperhub.pipelines.page_pdf import crop_png, page_pdf_path
from paperhub.pipelines.paper_asset import read_paper_asset
from paperhub.translation.language import canonical_language

logger = logging.getLogger(__name__)

IMAGE_UNAVAILABLE = "[image unavailable]"
# A figure image larger than this exports as unavailable.
MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ExportPaper:
    id: int
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    arxiv_id: str | None = None
    # The paper's cache dir: page crops and figure images come from it.
    source_dir: Path | None = None


@dataclass(frozen=True)
class MarkCite:
    """What an inline citation of a reading mark points at (a live mark)."""
    paper_id: int
    quote: str
    section: str | None = None
    page: int | None = None  # 0-based


# The default alias of an inline citation: the quote's first characters.
CITE_DEFAULT_CHARS = 24


def cite_default_text(quote: str) -> str:
    """An inline citation's text when the user gave none: the quote's first
    ``CITE_DEFAULT_CHARS`` characters (whitespace collapsed), then "…".
    The editor's ``citeDefaultText`` reads the same."""
    text = " ".join(quote.split())
    if len(text) <= CITE_DEFAULT_CHARS:
        return text
    return text[:CITE_DEFAULT_CHARS].rstrip() + "…"


# --- user text -------------------------------------------------------------

# Characters that carry no user meaning but can steer a Markdown parser or
# forge the exporter's own placeholders: C0/C1 controls except tab and line
# feed, U+2028/U+2029, the BOM, private use (the BMP area and planes
# 15-16), lone surrogates and the U+FFFE/U+FFFF noncharacters.
_UNSAFE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ufeff\ue000-\uf8ff"
    "\U000f0000-\U0010ffff\ud800-\udfff\ufffe\uffff]")


def _user_text(s: str) -> str:
    """The one normalisation every user string gets before it is written.

    ``\\r\\n`` and a lone ``\\r`` become ``\\n``: CommonMark ends a line at
    either, so a ``\\r`` would otherwise start a heading, list or image
    where the exporter sees one line. Then the characters in ``_UNSAFE``
    are dropped: controls other than ``\\t`` and ``\\n``, the Unicode line
    and paragraph separators, the BOM, private-use code points (the
    exporter's placeholders are built from them, so user text can never
    forge one), lone surrogates and noncharacters.

    Zero-width and bidi format characters (U+200B-U+200F, U+2060,
    U+202A-U+202E, U+2066-U+2069) are kept: ZWJ/ZWNJ carry meaning in
    emoji and several scripts, and none of them is Markdown syntax or
    whitespace to any reader, so they can neither open nor close a
    construct; every construct the exporter fears is escaped whatever
    stands next to it.
    """
    return _UNSAFE.sub("", s.replace("\r\n", "\n").replace("\r", "\n"))


def _clean(v: Any) -> Any:
    """``v`` with every string in it (nested lists and dict values) passed
    through ``_user_text``."""
    if isinstance(v, str):
        return _user_text(v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def _clean_inputs(
    note: Note, blocks: list[Block], papers: Mapping[int, ExportPaper],
) -> tuple[Note, list[Block], dict[int, ExportPaper]]:
    """The note, blocks and papers with all their text normalised: the
    single point user text enters the exporter."""
    note = note.model_copy(update={"title": _user_text(note.title)})
    blocks = [b.model_copy(update={
        "props": _clean(b.props), "content": _clean(b.content),
        "section_key": _clean(b.section_key)}) for b in blocks]
    cleaned = {pid: ExportPaper(
        id=p.id, title=_user_text(p.title),
        authors=[_user_text(a) for a in p.authors], year=p.year,
        arxiv_id=_clean(p.arxiv_id), source_dir=p.source_dir)
        for pid, p in papers.items()}
    return note, blocks, cleaned


# --- citations -------------------------------------------------------------

_CITATION = re.compile(
    r"[ \t]?\[(?:"
    r"chunk:\s*(?P<chunks>\d+(?:\s*,\s*(?:chunk:\s*)?\d+)*)"
    r"|fig:\s*(?P<figs>\d+\s*/\s*[A-Za-z0-9_.-]+"
    r"(?:\s*,\s*(?:fig:\s*)?\d+\s*/\s*[A-Za-z0-9_.-]+)*)"
    r"|view:\s*(?P<vpaper>\d+)\s*/\s*(?P<vpage>\d+)\s*/[\d\s,]+"
    r"|note:\s*[A-Za-z0-9_,:\s-]+"
    r"|unsourced:\s*[a-z_]+\s*"
    r")\]")


def _cited(blocks: Iterable[Block]) -> tuple[set[int], set[int]]:
    """Papers (``[fig:]`` / ``[view:]`` / mentions) and chunks the blocks cite."""
    papers: set[int] = set()
    chunks: set[int] = set()

    def scan(text: str) -> None:
        for m in _CITATION.finditer(text):
            if m["chunks"]:
                chunks.update(int(c) for c in re.findall(r"\d+", m["chunks"]))
            elif m["figs"]:
                papers.update(int(f.split("/")[0].replace("fig:", "").strip())
                              for f in m["figs"].split(","))
            elif m["vpaper"]:
                papers.add(int(m["vpaper"]))

    for b in blocks:
        for run in _all_runs(b):
            if run.get("type") == "mention":
                pid = (run.get("props") or {}).get("paperId")
                if isinstance(pid, int) and pid > 0:
                    papers.add(pid)
            elif isinstance(run.get("text"), str):
                scan(run["text"])
    return papers, chunks


# --- BibTeX ----------------------------------------------------------------

_STOP = frozenset({"a", "an", "the", "on", "of", "in", "for", "to", "and"})


def _ascii(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s).lower()
                   if "a" <= c <= "z")


def bib_key(p: ExportPaper) -> str:
    """``<firstauthor><year><firstword>``, e.g. ``vaswani2017attention``."""
    first = p.authors[0].split() if p.authors else []
    author = _ascii(first[-1]) if first else ""
    words = [_ascii(w) for w in p.title.split()]
    word = next((w for w in words if w and w not in _STOP), "")
    return f"{author or 'anon'}{p.year or 'nd'}{word}"


def _bib_value(s: str) -> str:
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return re.sub(r"([&%#_$])", r"\\\1", " ".join(s.split()))


def _bib_entry(key: str, p: ExportPaper) -> str:
    lines = [f"@misc{{{key},", f"  title = {{{{{_bib_value(p.title)}}}}},"]
    if p.authors:
        lines.append(
            f"  author = {{{' and '.join(_bib_value(a) for a in p.authors)}}},")
    if p.year:
        lines.append(f"  year = {{{p.year}}},")
    if p.arxiv_id:
        lines += [f"  eprint = {{{_bib_value(p.arxiv_id)}}},",
                  "  archivePrefix = {arXiv},"]
    lines.append("}")
    return "\n".join(lines)


# --- inline text -----------------------------------------------------------

# Every character Markdown (or a pandoc extension) gives a meaning inline.
# ``!`` keeps note text from ever forming an image: only the exporter's own
# references are images. ``$`` is literal: the editor has no inline math
# (math blocks and equations are written as math by the exporter).
_SPECIAL = re.compile(r"([\\`*_\[\]<>~^@|!${}])")
# A line start that would open a heading, list, quote or setext underline.
_LINE_START = re.compile(r"^([ \t\u00a0]*)(?:([#>+=-])|(\d+)([.)])(?=\s|$))", re.M)
# A code block's language as a plain class name: no ``{=openxml}`` raw
# block, no attributes.
_CODE_LANG = re.compile(r"^[A-Za-z0-9_+#.-]{1,32}$")
# Characters a link target keeps as written; the rest is percent-encoded,
# so a target can neither leave its ``<…>`` nor read as more Markdown.
_URL_SAFE = ":/?#@&=+,;%~-._*'"


# What a viewer turns into a link on its own: a scheme's ``:``
# (``http://``, ``mailto:``, GFM), the first ``/`` of ``//`` (linkify reads
# ``//host``) and a domain's ``.``: after ``w``/``W`` (``www.``) or between
# a host character and a letter (``e.com``; linkify viewers such as VS
# Code's preview). A host character is anything but a space or ASCII
# punctuation, so a combining mark or a joiner before the dot counts.
# Prose keeps its ``. `` and ``3.14`` as written. Where a run ends,
# ``_join_runs`` checks the same rules across the boundary.
_HOST = r"[^\s!-/:-@\[-`{-~]"
_HOST_CHAR = re.compile(_HOST)
_AUTOLINK = re.compile(rf":|/(?=/)|(?<=[wW])[.]|(?<={_HOST})[.](?=[^\W\d_])")


def _join_runs(prev: str, piece: str) -> tuple[str, str]:
    """``prev`` and ``piece`` (two escaped runs written side by side) with
    the ``_AUTOLINK`` rules applied across their boundary, where neither
    run alone could see the domain or the ``//``."""
    if not prev or not piece:
        return prev, piece
    if (prev.endswith(".") and not prev.endswith("\\.")
            and _HOST_CHAR.fullmatch(prev[-2:-1]) and piece[0].isalpha()):
        prev = prev[:-1] + "\\."
    elif (piece[0] == "." and _HOST_CHAR.fullmatch(prev[-1])
            and piece[1:2].isalpha()):
        piece = "\\" + piece
    elif prev.endswith("/") and not prev.endswith("\\/") and piece[0] == "/":
        prev = prev[:-1] + "\\/"
    return prev, piece


def _append(out: list[str], piece: str) -> None:
    """Append ``piece`` to the escaped runs in ``out`` with the
    ``_AUTOLINK`` rules applied across the join. An empty piece is skipped,
    so the runs on either side of it still meet."""
    if not piece:
        return
    if out:
        out[-1], piece = _join_runs(out[-1], piece)
    out.append(piece)


def _escape(text: str) -> str:
    """Markdown-escape ``text`` (inline text of one block)."""
    return _AUTOLINK.sub(lambda m: "\\" + m[0], _SPECIAL.sub(r"\\\1", text))


_LEAD = re.compile(r"^[ \t]+", re.M)
NBSP = "\u00a0"


def _lead(m: re.Match[str]) -> str:
    return m[0].replace("\t", NBSP * 4).replace(" ", NBSP)


def _line_starts(text: str) -> str:
    """Escape every line start that would read as block syntax. Leading
    spaces become no-break spaces: they keep the indent but never start an
    indented code block."""
    text = _LEAD.sub(_lead, text)

    def sub(m: re.Match[str]) -> str:
        if m[2]:
            return f"{m[1]}\\{m[2]}"
        return f"{m[1]}{m[3]}\\{m[4]}"
    return _LINE_START.sub(sub, text)


def _one_line(text: str) -> str:
    """A title on one line: its line breaks folded into spaces."""
    return " ".join(text.split())


_BARE_DOLLAR = re.compile(r"(?<!\\)((?:\\\\)*)\$")


def _math(latex: str) -> str:
    """LaTeX for ``$$…$$``: a ``$`` becomes ``\\$`` (so nothing closes the
    math early) and ``![`` / ``](`` / ``<`` get an empty group ``{}``
    inside them, so the LaTeX never reads as an image, a link, an autolink
    or HTML in a Markdown viewer. ``//`` and a dotted name stay as written:
    an empty group there would break ``\\label{eq.a}`` / ``\\tag{1.b}`` and
    pandoc would write raw TeX into the ``.docx``. Blank lines are dropped
    (they would end the math)."""
    # Continuation lines are indented 4 spaces: inside a paragraph that
    # starts no fence, heading, quote or list in any reader (LaTeX ignores
    # the spaces), so a GFM viewer keeps the whole math between its $$.
    out = "\n    ".join(ln.strip() for ln in latex.splitlines() if ln.strip())
    # Every $ ends up after an odd run of backslashes (a bare one gets one
    # more; an escaped \$ is kept), so no reader takes it as a delimiter.
    out = _BARE_DOLLAR.sub(r"\1\\$", out)
    out = (out.replace("![", "!{}[").replace("](", "]{}(")
           .replace("<", "<{}"))
    # A trailing backslash would escape the closing $$ in some viewers.
    return out + " " if out.endswith("\\") else out


def _hard_breaks(text: str) -> str:
    """Line breaks inside a block kept as hard breaks, each line escaped."""
    return "\\\n".join(_line_starts(text).split("\n"))


def _runs(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return [{"type": "text", "text": content}] if isinstance(content, str) else []
    return [r for r in content if isinstance(r, dict)]


def _all_runs(b: Block) -> list[dict[str, Any]]:
    """The runs of a block, a table's cells included."""
    if b.type == "table" and isinstance(b.content, dict):
        try:
            return [r for row in table_rows(b.content) for c in row for r in c]
        except ValueError:
            return []
    return _runs(b.content)


def _json(v: Any, default: Any) -> Any:
    """A prop the editor may store as a JSON string (``pieces``,
    ``source``), decoded. What it decodes is normalised like every other
    user string: the JSON text's escapes (``\\r``, a lone surrogate) only
    become characters here, after ``_clean_inputs`` ran."""
    if isinstance(v, str):
        try:
            v = _clean(json.loads(v))
        except ValueError:
            return default
    return v if isinstance(v, type(default)) else default


def _wrap(text: str, mark: str, end: str | None = None) -> str:
    """``mark`` + text + ``end``, whitespace kept outside the markers."""
    core = text.strip()
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return f"{lead}{mark}{core}{end if end is not None else mark}{trail}"


def _code_span(code: str) -> str:
    """An inline code span that holds ``code`` whole (CommonMark): fenced by
    one backtick more than its longest backtick run, padded with a space
    when it starts or ends with a backtick. Edge whitespace stays outside,
    where every reader keeps it."""
    core = code.strip()
    if not core:
        return code
    longest = max((len(r) for r in re.findall(r"`+", core)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if core[0] == "`" or core[-1] == "`" else ""
    return _wrap(code, fence + pad, pad + fence)


# A held code span's placeholder: private-use delimiters (which
# ``_user_text`` drops from every user string, so note text can never hold
# one) around a per-export random nonce and the span's index.
# A pipe in math: ``\|`` or ``|``. A text-mode command (``\text{``) is
# matched first, and a ``\\`` kept.
_MATH_BAR = re.compile(
    r"(\\(?:text|textbf|textit|textrm|textsf|texttt|mbox)\{)|\\\\|\\\||\|")


def _text_group(latex: str, start: int, opener: str) -> tuple[str, int]:
    r"""The text-mode group opened at ``start`` (just after its ``{``) with
    each pipe written as ``}\vert{}<opener>`` (text mode has no pipe that
    texmath reads), and where it ends. Braces nested in the group are
    closed and reopened around the pipe."""
    out: list[str] = []
    depth = 1
    i = start
    while i < len(latex) and depth:
        c = latex[i]
        if c == "\\" and i + 1 < len(latex):
            out.append(latex[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "|":
            out.append("}" * depth + "\\vert{}" + opener + "{" * (depth - 1))
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out), i


def _math_bars(latex: str) -> str:
    r"""``latex`` with every pipe a command, so no table reader splits a
    cell on it: ``\vert{}`` / ``\Vert{}`` (``{}`` ends the command: math
    may not end in a space before ``$``)."""
    out: list[str] = []
    pos = 0
    while True:
        m = _MATH_BAR.search(latex, pos)
        if m is None:
            out.append(latex[pos:])
            return "".join(out)
        out.append(latex[pos:m.start()])
        t = m[0]
        pos = m.end()
        if m[1]:
            body, pos = _text_group(latex, pos, t)
            out.append(t + body)
        else:
            out.append({"\\\\": t, "\\|": "\\Vert{}"}.get(t, "\\vert{}"))


# A URL argument of ``\href`` / ``\url``: left as written.
_MATH_URL = re.compile(r"\\(?:href|url)\{[^{}]*\}")


def _unlink(latex: str) -> str:
    """``latex`` with a bare URL / ``www.`` broken by an empty group, so a
    viewer's linkify cannot make it a link; ``\\href`` / ``\\url``
    arguments stay as written."""
    out: list[str] = []
    pos = 0
    for m in _MATH_URL.finditer(latex):
        out.append(_unlink_plain(latex[pos:m.start()]) + m[0])
        pos = m.end()
    out.append(_unlink_plain(latex[pos:]))
    return "".join(out)


def _unlink_plain(s: str) -> str:
    return s.replace("://", ":/{}/").replace("www.", "www{}.")


_HELD = re.compile(r"\ue000[0-9a-f]{16}:\d+\ue001")


class _Writer:
    def __init__(self, note: Note, papers: Mapping[int, ExportPaper],
                 chunk_papers: Mapping[int, int],
                 mark_papers: Mapping[str, int],
                 mark_cites: Mapping[str, MarkCite] | None = None) -> None:
        self.note = note
        self.mark_cites = mark_cites or {}
        # Inline citations' footnotes, in order: ``[^n]`` is the n-th.
        self.footnotes: list[str] = []
        self.papers = papers
        self.chunk_papers = chunk_papers
        self.mark_papers = mark_papers
        self.images: dict[str, bytes] = {}
        self.cited: list[int] = []  # papers in first-cited order
        # Keys are given as papers are first cited, so a collision suffix is
        # only ever spent on a paper that is in references.bib.
        self.keys: dict[int, str] = {}
        self._bases: dict[str, int] = {}
        self._assets: dict[int, Any] = {}
        self._image_count = 0
        # Inline code spans, held out of the line-level escaping (hard
        # breaks, line starts, indents) until the block is written.
        self._held: dict[str, str] = {}
        # For each table written, in order: a notes paragraph follows it.
        self.table_notes: list[bool] = []
        self._nonce = secrets.token_hex(8)

    def cite(self, pid: int | None, locator: str = "") -> str:
        if pid is None or pid not in self.papers:
            return ""
        if pid not in self.keys:
            base = bib_key(self.papers[pid])
            n = self._bases.get(base, 0)
            used = set(self.keys.values())
            while True:
                key = base if n == 0 else base + (chr(ord("a") + n - 1) if n <= 26 else str(n))
                n += 1
                if key not in used:
                    break
            self._bases[base] = n
            self.keys[pid] = key
            self.cited.append(pid)
        return f"@{self.keys[pid]}{locator}"

    def _citation(self, m: re.Match[str]) -> str:
        cites: list[str] = []
        if m["chunks"]:
            for c in re.findall(r"\d+", m["chunks"]):
                cites.append(self.cite(self.chunk_papers.get(int(c))))
        elif m["figs"]:
            for f in m["figs"].split(","):
                pid = f.split("/")[0].replace("fig:", "").strip()
                cites.append(self.cite(int(pid)))
        elif m["vpaper"]:
            cites.append(self.cite(int(m["vpaper"]), f", p. {int(m['vpage'])}"))
        lead = " " if m.group(0)[0] in " \t" else ""
        if "unsourced" in m.group(0):
            return f"{lead}(unsourced)"
        kept = list(dict.fromkeys(c for c in cites if c))
        return f"{lead}[{'; '.join(kept)}]" if kept else ""

    def text(self, raw: str) -> str:
        """Escaped text with its citations as ``[@key]``. The pieces are
        joined through ``_append``, so a citation that renders as nothing
        cannot glue ``e.`` and ``com`` into a bare domain."""
        out: list[str] = []
        pos = 0
        for m in _CITATION.finditer(raw):
            _append(out, _escape(raw[pos:m.start()]))
            _append(out, self._citation(m))
            pos = m.end()
        _append(out, _escape(raw[pos:]))
        return "".join(out)

    def inline(self, content: Any) -> str:
        out: list[str] = []
        for run in _runs(content):
            kind = run.get("type", "text")
            if kind == "link":
                href = quote(str(run.get("href") or ""), safe=_URL_SAFE)
                label = self.inline(run.get("content")) or _escape(href)
                _append(out, f"[{label}](<{href}>)")
            elif kind == "mention":
                props = run.get("props") or {}
                label = _escape(str(props.get("label") or ""))
                pid = props.get("paperId")
                key = self.cite(pid if isinstance(pid, int) else None)
                _append(out, f"{label} [{key}]" if key else label)
            elif kind == "markCite":
                _append(out, self.mark_cite(run.get("props") or {}))
            elif kind == "noteLink":
                _append(out, _escape(link_text(run.get("props") or {})))
            elif kind == "inlineMath":
                latex = " ".join(str((run.get("props") or {}).get("latex") or "").split())
                if latex:
                    # Held, like a code span: the text escaping must not touch it.
                    # A backtick would pair with another one in the line and
                    # open a code span across it: LaTeX reads it as a
                    # left quote anyway.
                    latex = latex.replace("`", "‘")
                    # A viewer's linkify would make a URL here a link that
                    # runs on into the text after the math.
                    latex = _unlink(latex)
                    _append(out, self._hold(f"${_math(latex).rstrip()}$"))
            elif isinstance(run.get("text"), str):
                _append(out, self._styled(run))
        return "".join(out)

    def mark_cite(self, props: dict[str, Any]) -> str:
        """An inline citation of a mark: its alias and a footnote with the
        quote, the paper and where in it. A deleted mark's citation is its
        alias only."""
        cite = self.mark_cites.get(str(props.get("markId") or ""))
        alias = str(props.get("text") or "").strip()
        if cite is None:
            return _escape(alias)
        alias = alias or cite_default_text(cite.quote)
        where: list[str] = []
        paper = self.papers.get(cite.paper_id)
        if paper is not None and paper.title.strip():
            where.append(_escape(_one_line(paper.title)))
        if cite.section and cite.section.strip():
            where.append(_escape(section_label(_one_line(cite.section))))
        if cite.page is not None:
            where.append(f"p. {cite.page + 1}")
        quote = _escape(" ".join(cite.quote.split()))
        note = f'"{quote}"' + (" — " + ", ".join(where) if where else "")
        self.footnotes.append(note)
        return _escape(alias) + self._hold(f"[^{len(self.footnotes)}]")

    def _styled(self, run: dict[str, Any]) -> str:
        styles = run.get("styles") or {}
        if styles.get("code"):
            # A code span cannot hold a line break: it reads as a space.
            code = " ".join(run["text"].split("\n"))
            core = code.strip()
            if not core:
                return code
            # Only the span is held: its edge whitespace stays in the text,
            # where the line-start escaping sees it.
            lead = code[: len(code) - len(code.lstrip())]
            trail = code[len(code.rstrip()):]
            return lead + self._hold(_code_span(core)) + trail
        # ``hidden`` and ``comment`` add nothing: plain text.
        text = self.text(run["text"])
        if styles.get("strike"):
            text = _wrap(text, "~~")
        if styles.get("underline"):
            text = _wrap(text, "[", "]{.underline}")
        if styles.get("italic"):
            text = _wrap(text, "*")
        if styles.get("bold"):
            text = _wrap(text, "**")
        return text

    # --- paper references --------------------------------------------------

    def block_paper(self, b: Block) -> int | None:
        """A reference block's paper: its own prop, else its mark's paper,
        else the note's paper."""
        pid = b.props.get("paperContentId")
        if isinstance(pid, int):
            return pid
        mark = b.props.get("markId")
        if isinstance(mark, str) and mark in self.mark_papers:
            return self.mark_papers[mark]
        if self.note.target_kind == "paper" and self.note.target_id.isdigit():
            return int(self.note.target_id)
        return None

    def source_line(self, b: Block, pid: int | None) -> str:
        source = _json(b.props.get("source"), {})
        parts: list[str] = []
        paper = self.papers.get(pid) if pid is not None else None
        if paper is not None:
            parts.append(_escape(_one_line(paper.title)))
        section = source.get("section") or b.section_key
        if isinstance(section, str) and section.strip():
            parts.append(_escape(section_label(_one_line(section))))
        page = source.get("page")
        if isinstance(page, int) and not isinstance(page, bool):
            parts.append(f"p. {page + 1}")
        key = self.cite(pid)
        line = "— " + ", ".join(parts) if parts else ""
        if key:
            line = f"{line} [{key}]" if line else f"[{key}]"
        return line

    def image(self, b: Block, pid: int | None) -> tuple[str, bytes] | None:
        """The block's image file: the figure's own image, else a page crop."""
        paper = self.papers.get(pid) if pid is not None else None
        live = live_pieces(_json(b.props.get("pieces"), []))
        piece = live[0] if live else {}
        if pid is None or paper is None or paper.source_dir is None:
            return None
        try:
            fig_id = piece.get("figure_id")
            if isinstance(fig_id, str):
                if pid not in self._assets:
                    self._assets[pid] = read_paper_asset(paper.source_dir)
                asset = self._assets[pid]
                fig = next((f for f in (asset.figures if asset else [])
                            if f.id == fig_id), None)
                path = fig.abs_image_path(paper.source_dir) if fig else None
                if path is not None and path.is_file():
                    if path.stat().st_size > MAX_IMAGE_BYTES:
                        logger.warning("note export: figure image of block %s is "
                                       "over %d bytes", b.id, MAX_IMAGE_BYTES)
                        return None
                    ext = path.suffix.lower()
                    ext = ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ".png"
                    return self._name(ext), path.read_bytes()
            box = piece.get("box")
            page = piece.get("page")
            if isinstance(box, list) and len(box) == 4 and isinstance(page, int):
                pdf = page_pdf_path(paper.source_dir)
                frac = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
                png = crop_png(pdf, page, frac) if pdf else None
                if png:
                    return self._name(".png"), png
        except Exception:  # noqa: BLE001 - one missing image never fails the export
            logger.warning("note export: no image for block %s", b.id, exc_info=True)
        return None

    def _hold(self, span: str) -> str:
        token = f"\ue000{self._nonce}:{len(self._held)}\ue001"
        self._held[token] = span
        return token

    def escape_held_pipes(self, text: str) -> None:
        """Make the held spans in one table cell safe for a row: a pipe in
        a code span is ``\\|``, in math ``\\vert{}`` / ``\\Vert{}`` (a table
        reader splits cells on it first), and a line break folds to a
        space. A code span with a backslash before a pipe cannot be
        written in a GFM table, so it becomes escaped plain text."""
        for token in _HELD.findall(text):
            span = self._held.get(token, "")
            if span.startswith("`"):
                inner = span.strip("`")
                if "|" not in inner:
                    continue
                if "\\|" in inner:
                    # Only the span's own padding goes: the core has no
                    # edge whitespace, and interior spaces stay as typed.
                    if inner[:1] == inner[-1:] == " ":
                        inner = inner[1:-1]
                    self._held[token] = _escape(inner)
                else:
                    self._held[token] = span.replace("|", "\\|")
            elif span.startswith("$"):
                span = re.sub(r"\s*\n\s*", " ", span)
                self._held[token] = _math_bars(span)

    def release(self, text: str) -> str:
        """``text`` with its held code spans put back, each at the one place
        its token was written. Only this export's own tokens are replaced,
        each once; anything else of that shape is dropped."""
        return _HELD.sub(lambda m: self._held.pop(m[0], ""), text)

    def _name(self, ext: str) -> str:
        """The next image file name: by index, never by block id (an id
        like CON or AUX is a device name on Windows)."""
        self._image_count += 1
        return f"images/{self._image_count:04d}{ext}"

    def figure(self, b: Block, pid: int | None, caption: str) -> list[str]:
        got = self.image(b, pid)
        cap = " ".join(caption.split())
        if got is None:
            return [p for p in (_line_starts(self.text(cap)), IMAGE_UNAVAILABLE) if p]
        name, data = got
        self.images[name] = data
        return [f"![{self.text(cap)}]({name})"]


def section_label(key: str) -> str:
    """"§2" for a numbered section key ("2 Method"), else "§ Name"."""
    m = re.match(r"^(\d+(?:\.\d+)*)\b", key.strip())
    return f"§{m[1]}" if m else f"§ {key.strip()}"


_LANG_SHORT = {"繁體中文": "繁中", "简体中文": "简中"}


def _quoted(lines: list[str]) -> str:
    return "\n".join(f"> {ln}" if ln else ">" for ln in lines)


_LIST_MARKER = {"bulletListItem": "-", "numberedListItem": "1.",
                "checkListItem": "-"}
_LIST = set(_LIST_MARKER)
_REF_TYPES = frozenset({"paperQuote", "paperFigure", "paperEquation", "paperRegion"})


_ALIGN_ROW = {"left": "---", "center": ":-:", "right": "--:", "": "---"}


def _written_rows(b: Block) -> list[list[list[dict[str, Any]]]]:
    """The rows of a table block as the export writes them (none: the
    table is not written)."""
    content = b.content if isinstance(b.content, dict) else {}
    try:
        return table_rows(content)
    except ValueError:
        return []


def _notes_lines(b: Block) -> list[str]:
    return [ln for ln in str(b.props.get("notes") or "").split("\n") if ln.strip()]


def _table_text(w: _Writer, raw: str) -> str:
    """One line of a table's caption or notes: its ``$…$`` kept as math,
    the rest escaped (as in a quote)."""
    return " ".join(p for p in _quote_paragraphs(w, " ".join(raw.split())) if p)


def _table(w: _Writer, b: Block) -> str:
    """A pipe table: the header row (empty when the table has none), the
    alignment row, then the body. Every cell goes through ``w.inline``.
    A caption follows as pandoc's ``Table: <caption>`` line, then the notes
    as one paragraph (a hard break per line)."""
    content = b.content if isinstance(b.content, dict) else {}
    rows = _written_rows(b)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    # Alignment is read from row 0, also when headerRows is 0.
    aligns = (table_aligns(content) + [""] * width)[:width]

    def cell(runs: list[dict[str, Any]]) -> str:
        text = " ".join(w.inline(runs).split("\n"))
        w.escape_held_pipes(text)
        return text.strip()

    def line(cells: list[str]) -> str:
        cells = cells + [""] * (width - len(cells))
        return "| " + " | ".join(cells) + " |"

    body = [[cell(c) for c in r] for r in rows]
    header_rows = 1 if content.get("headerRows") else 0
    head = body[0] if header_rows else [""] * width
    rest = body[header_rows:]
    out = [line(head), line([_ALIGN_ROW[a] for a in aligns])]
    out += [line(r) for r in rest]
    caption = _table_text(w, str(b.props.get("caption") or ""))
    if caption:
        out += ["", f"Table: {caption}"]
    notes = [t for t in (_table_text(w, ln) for ln in _notes_lines(b)) if t]
    if notes:
        out += ["", "\\\n".join(notes)]
    w.table_notes.append(bool(notes))
    return "\n".join(out)


def _block(w: _Writer, b: Block, heading_offset: int) -> str:
    t = b.type
    if t == "section":
        title = _one_line(str(b.props.get("title") or b.section_key or ""))
        return f"## {w.text(title)}"
    if t == "heading":
        level = b.props.get("level")
        level = level if isinstance(level, int) else 1
        title = " ".join(w.inline(b.content).split("\n"))
        return f"{'#' * min(6, level + heading_offset)} {title}"
    if t == "divider":
        return "---"
    if t == "math":
        latex = _math(str(b.props.get("latex") or ""))
        return f"$${latex}$$" if latex else ""
    if t == "codeBlock":
        code = "".join(str(r.get("text", "")) for r in _runs(b.content))
        code = code or str(b.props.get("code") or "")
        fence = "```"
        while fence in code:
            fence += "`"
        lang = str(b.props.get("language") or "").strip()
        if not _CODE_LANG.match(lang):
            lang = ""
        return f"{fence}{lang}\n{code}\n{fence}"
    if t in ("quote", "callout"):
        return _quoted(_hard_breaks(w.inline(b.content)).split("\n"))
    if t == "bulletListItem":
        return f"- {_hard_breaks(w.inline(b.content))}"
    if t == "numberedListItem":
        return f"1. {_hard_breaks(w.inline(b.content))}"
    if t == "checkListItem":
        box = "x" if b.props.get("checked") else " "
        return f"- [{box}] {_hard_breaks(w.inline(b.content))}"
    if t == "table":
        return _table(w, b)
    if t == "paperQuote":
        return _paper_quote(w, b)
    if t in ("paperFigure", "paperEquation", "paperRegion"):
        return _paper_media(w, b)
    return _hard_breaks(w.inline(b.content))


# Same rules as the frontend's ``lib/quoteMath.ts``: ``$…$`` in a quote from
# the paper's reading text is math (an escaped ``\$`` is a dollar sign).
_QUOTE_MATH = re.compile(r"(?<!\\)\$((?:\\\$|[^$])+?)\$")
_DISPLAY_ENV = re.compile(
    r"\\begin\s*\{(?:aligned|align\*?|equation\*?|gather\*?|multline\*?"
    r"|array|cases|split)\}")
_LABEL = re.compile(r"\\label\s*\{[^{}]*\}")


def _quote_paragraphs(w: _Writer, raw: str, prefix: str = "") -> list[str]:
    """One quote line as Markdown paragraphs: its ``$…$`` kept as math, the
    text around it escaped. A multi-line environment is display math in a
    paragraph of its own. A line without math is ``w.text`` as before."""
    paras: list[str] = []
    cur: list[str] = [prefix]

    split = False  # a display block was written: trim the text beside it

    def flush(trim: bool) -> None:
        text = "".join(cur)
        cur.clear()
        if trim:
            text = text.strip(" \t")
        if text.strip():
            paras.append(_line_starts(text))

    pos = 0
    for m in _QUOTE_MATH.finditer(raw):
        latex = " ".join(_LABEL.sub("", m[1]).split())
        if not latex or latex.endswith("\\"):
            continue  # not writable as math: stays escaped text
        cur.append(w.text(raw[pos:m.start()]))
        if _DISPLAY_ENV.search(latex):
            flush(True)
            paras.append(f"$${_math(latex)}$$")
            split = True
        else:
            cur.append(f"${_math(latex)}$")
        pos = m.end()
    cur.append(w.text(raw[pos:]))
    flush(split)
    if not paras:
        paras = [""]
    return paras


def _paper_quote(w: _Writer, b: Block) -> str:
    pid = w.block_paper(b)
    live = live_pieces(_json(b.props.get("pieces"), []))
    quote = joined_quote(live) if live else ""
    quote = quote or str(b.props.get("quote") or "")
    # Each paragraph of the quote is its own paragraph in the block quote.
    lines: list[str] = []
    for ln in quote.split("\n"):
        for para in _quote_paragraphs(w, ln):
            lines += ["", para] if lines else [para]
    lang = b.props.get("lang")
    if isinstance(lang, str) and lang.strip():
        label = canonical_language(lang)
        short = _one_line(_LANG_SHORT.get(label, label))
        lines += ["", f"*Translated · {_escape(short)}*"]
        orig = b.props.get("orig")
        if isinstance(orig, str) and orig.strip():
            first, *rest = _quote_paragraphs(w, " ".join(orig.split()), "Original: ")
            lines += ["", first, *[x for p in rest for x in ("", p)]]
    source = w.source_line(b, pid)
    if source:
        lines += ["", source]
    return _quoted(lines)


def _paper_media(w: _Writer, b: Block) -> str:
    pid = w.block_paper(b)
    caption = str(b.props.get("quote") or "")
    parts: list[str] = []
    if b.type == "paperEquation":
        live = live_pieces(_json(b.props.get("pieces"), []))
        latex = live[0].get("latex") if live else None
        if isinstance(latex, str) and latex.strip():
            parts.append(f"$${_math(' '.join(latex.split()))}$$")
            caption = caption or latex
        got = w.image(b, pid)
        if got is not None:
            w.images[got[0]] = got[1]
            parts.append(f"![{w.text(' '.join(caption.split()))}]({got[0]})")
        elif not parts:
            parts += [_line_starts(w.text(" ".join(caption.split()))),
                      IMAGE_UNAVAILABLE]
    else:
        parts += w.figure(b, pid, caption)
    source = w.source_line(b, pid)
    if source:
        parts.append(source)
    return "\n\n".join(p for p in parts if p)


_SLUG_DROP = re.compile(r"[^\w\-.\s]")


def pandoc_slug(text: str) -> str:
    """The id pandoc (``auto_identifiers``) gives a heading: everything but
    letters, digits, ``_ - .`` and spaces dropped, spaces to hyphens, lower
    case, everything before the first letter removed, else ``section``.
    (A repeat gets ``-1``, ``-2`` in ``_toc``.) The links match pandoc (the
    ``.docx`` path, where ``markdown_to_docx`` moves both the heading ids
    and these links to ``h-<slug>``); a GFM viewer may not resolve some of them (it keeps
    leading digits and may treat non-ASCII differently)."""
    s = "-".join(_SLUG_DROP.sub("", text).lower().split())
    first = next((i for i, ch in enumerate(s) if ch.isalpha()), len(s))
    return s[first:] or "section"


def _toc(blocks: list[Block], title: str) -> str:
    """A ``toc`` block: the note's headings 1-3 and sections (level 1) as a
    nested list, each linked to the id pandoc gives that heading. The
    note's own title heading takes its id first."""
    seen: dict[str, int] = {}

    def unique(slug: str) -> str:
        n = seen.get(slug)
        seen[slug] = (n or 0) + 1
        return slug if n is None else f"{slug}-{n}"

    unique(pandoc_slug(title))
    lines: list[str] = []
    # The levels of the entries the next one may nest under: a level 2
    # after a level 3 steps back out to its level 2 sibling.
    open_levels: list[int] = []
    for b in blocks:
        if b.type == "section":
            text = str(b.props.get("title") or b.section_key or "")
            level = 1
        elif b.type == "heading":
            lv = b.props.get("level")
            text = read_runs(b.content)
            level = lv if isinstance(lv, int) else 1
        else:
            continue
        text = _one_line(_user_text(text))
        if not text or not 1 <= level <= 3:
            continue
        slug = unique(pandoc_slug(text))
        while open_levels and open_levels[-1] >= level:
            open_levels.pop()
        depth = len(open_levels)
        open_levels.append(level)
        lines.append(f"{'  ' * depth}- [{_escape(text)}](#{slug})")
    return "\n".join(lines)


def note_to_markdown(
    note: Note, blocks: list[Block], papers: Mapping[int, ExportPaper], *,
    chunk_papers: Mapping[int, int] | None = None,
    mark_papers: Mapping[str, int] | None = None,
    mark_cites: Mapping[str, MarkCite] | None = None,
    table_notes: list[bool] | None = None,
) -> tuple[str, dict[str, bytes], str]:
    """``(markdown, image files by relative path, BibTeX)`` of a note.
    ``blocks`` in document order; ``chunk_papers`` maps a cited chunk id to
    its paper, ``mark_papers`` a reference block's mark id to its paper.
    ``table_notes``, when given, is filled with one flag per table written:
    whether its notes paragraph follows it (for ``markdown_to_docx``)."""
    note, blocks, papers = _clean_inputs(note, blocks, papers)
    w = _Writer(note, papers, chunk_papers or {}, mark_papers or {},
                mark_cites)
    offset = 2 if any(b.type == "section" for b in blocks) else 1
    indent: dict[str, str] = {}
    types = {b.id: b.type for b in blocks}
    out = f"# {_escape(_one_line(note.title))}"
    prev_marker: str | None = None
    for b in blocks:
        parent = b.parent_id
        pad = ""
        if parent in indent:
            pad = indent[parent] + ("    " if types.get(parent) in _LIST else "")
        indent[b.id] = pad
        if b.type == "toc":
            body = _toc(blocks, _one_line(note.title))
        else:
            body = w.release(_block(w, b, offset))
        if not body.strip():
            continue
        marker = _LIST_MARKER.get(b.type)
        body = "\n".join(pad + ln if ln else ln for ln in body.split("\n"))
        # Items of one list stay tight; a nested item continues its list.
        tight = marker is not None and (marker == prev_marker or bool(pad))
        out += ("\n" if tight else "\n\n") + body
        prev_marker = marker
    # Inline citations' footnotes close the note (pandoc: Word footnotes).
    for i, text in enumerate(w.footnotes, 1):
        out += f"\n\n[^{i}]: {text}"
    bib = "\n\n".join(_bib_entry(w.keys[pid], w.papers[pid]) for pid in w.cited)
    if table_notes is not None:
        table_notes[:] = w.table_notes
    return out.rstrip() + "\n", w.images, (bib + "\n" if bib else "")


# --- loading ---------------------------------------------------------------

async def export_sources(
    conn: aiosqlite.Connection, note: Note, blocks: list[Block],
) -> tuple[dict[int, ExportPaper], dict[int, int], dict[str, int],
           dict[str, MarkCite]]:
    """The papers a note's export may cite (its own paper, the papers of
    its reference blocks, mentioned and cited ones), the paper of each chunk
    it cites, the paper of each reference block's mark and what each inline
    citation's live mark quotes."""
    pids, chunk_ids = _cited(blocks)
    cite_ids = {str((r.get("props") or {}).get("markId") or "")
                for b in blocks for r in _all_runs(b)
                if r.get("type") == "markCite"} - {""}
    mark_ids: set[str] = set()
    for b in blocks:
        if b.type not in _REF_TYPES:
            continue
        own = b.props.get("paperContentId")
        if isinstance(own, int):
            pids.add(own)
        elif isinstance(b.props.get("markId"), str) and b.props["markId"]:
            mark_ids.add(b.props["markId"])
    mark_papers: dict[str, int] = {}
    if mark_ids:
        marks = ",".join("?" * len(mark_ids))
        async with conn.execute(
            f"SELECT id, paper_content_id FROM note_marks WHERE id IN ({marks})",  # noqa: S608
            tuple(mark_ids),
        ) as cur:
            mark_papers = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
    mark_cites: dict[str, MarkCite] = {}
    if cite_ids:
        marks = ",".join("?" * len(cite_ids))
        async with conn.execute(
            "SELECT id, paper_content_id, pieces_json, section_key "
            f"FROM note_marks WHERE id IN ({marks}) "  # noqa: S608
            "AND deleted_at IS NULL",
            tuple(cite_ids),
        ) as cur:
            for r in await cur.fetchall():
                live = live_pieces(_json(r[2], []))
                page = live[0].get("page") if live else None
                mark_cites[str(r[0])] = MarkCite(
                    paper_id=int(r[1]), quote=joined_quote(live),
                    section=r[3] or None,
                    page=page if isinstance(page, int) else None)
    pids |= set(mark_papers.values())
    pids |= {c.paper_id for c in mark_cites.values()}
    chunk_papers: dict[int, int] = {}
    if chunk_ids:
        marks = ",".join("?" * len(chunk_ids))
        async with conn.execute(
            f"SELECT id, paper_content_id FROM chunks WHERE id IN ({marks})",  # noqa: S608
            tuple(chunk_ids),
        ) as cur:
            chunk_papers = {int(r[0]): int(r[1]) for r in await cur.fetchall()}
    pids |= set(chunk_papers.values())
    if note.target_kind == "paper" and note.target_id.isdigit():
        pids.add(int(note.target_id))
    papers: dict[int, ExportPaper] = {}
    if pids:
        marks = ",".join("?" * len(pids))
        async with conn.execute(
            "SELECT id, title, authors_json, year, arxiv_id, source_dir_path "
            f"FROM paper_content WHERE id IN ({marks})",  # noqa: S608
            tuple(pids),
        ) as cur:
            for r in await cur.fetchall():
                authors = _json(r[2], [])
                papers[int(r[0])] = ExportPaper(
                    id=int(r[0]), title=str(r[1] or ""),
                    authors=[str(a) for a in authors if str(a).strip()],
                    year=int(r[3]) if r[3] else None, arxiv_id=r[4] or None,
                    source_dir=Path(r[5]) if r[5] else None)
    return papers, chunk_papers, mark_papers, mark_cites


# Bidi marks, overrides and isolates: in a file name they only reorder
# how the name reads, so names drop them (U+200E, U+200F, U+061C,
# U+202A-U+202E, U+2066-U+2069).
_BIDI = re.compile(f"[{chr(0x200E)}{chr(0x200F)}{chr(0x061C)}"
                   f"{chr(0x202A)}-{chr(0x202E)}{chr(0x2066)}-{chr(0x2069)}]")


def slug(title: str) -> str:
    """A file-name slug of a note's title ("note" when nothing is left)."""
    s = unicodedata.normalize("NFKD", _user_text(title))
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60].rstrip("-") or "note"


def display_name(title: str) -> str:
    """A note's title as a file name: normalised like note text, bidi
    controls dropped, path and control characters replaced ("note" when
    nothing is left)."""
    s = _BIDI.sub("", _user_text(title))
    s = re.sub(r'[\/:*?"<>|\x00-\x1f]+', "-", s)
    s = " ".join(s.split()).strip(" .-")
    return s[:100] or "note"
