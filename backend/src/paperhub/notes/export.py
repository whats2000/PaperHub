"""A note as Markdown: the text, its images and a BibTeX file of its sources.

Sections become ``##`` headings; a quote from a paper a block quote with
"— <paper>, §<section>, p. <page> [@key]" under it; figures, equations and
boxes an image file (the figure's own image, else a page crop) with its
caption; math ``$…$`` / ``$$…$$``; dividers ``---``. Citations
(``[chunk:N]``, ``[fig:P/ID]``, ``[view:P/N/box]``, an ``@`` mention of a
paper) become ``[@key]`` with one ``references.bib`` entry per paper cited.

Comment threads are never note text: a commented range exports as plain
text and the thread stays out. Hidden text exports as plain text.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from paperhub.notes.marks import joined_quote, live_pieces
from paperhub.notes.models import Block, Note
from paperhub.pipelines.page_pdf import crop_png, page_pdf_path
from paperhub.pipelines.paper_asset import read_paper_asset
from paperhub.translation.language import canonical_language

logger = logging.getLogger(__name__)

IMAGE_UNAVAILABLE = "[image unavailable]"


@dataclass(frozen=True)
class ExportPaper:
    id: int
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    arxiv_id: str | None = None
    # The paper's cache dir: page crops and figure images come from it.
    source_dir: Path | None = None


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
        for run in _runs(b.content):
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

_MATH = re.compile(r"(\$\$[^$]+\$\$|\$[^$\n]+\$)")
_SPECIAL = re.compile(r"([\\`*_\[\]<>~^@|])")
_LINE_START = re.compile(r"^(\s*)([#>+-]|\d+[.)])(?=\s|$)")


def _escape(text: str) -> str:
    """Markdown-escape ``text``, leaving ``$…$`` math as written."""
    parts = _MATH.split(text)
    return "".join(p if i % 2 else _SPECIAL.sub(r"\\\1", p)
                   for i, p in enumerate(parts))


def _runs(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return [{"type": "text", "text": content}] if isinstance(content, str) else []
    return [r for r in content if isinstance(r, dict)]


def _json(v: Any, default: Any) -> Any:
    if isinstance(v, str):
        try:
            v = json.loads(v)
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


class _Writer:
    def __init__(self, note: Note, papers: Mapping[int, ExportPaper],
                 chunk_papers: Mapping[int, int]) -> None:
        self.note = note
        self.papers = papers
        self.chunk_papers = chunk_papers
        self.images: dict[str, bytes] = {}
        self.cited: list[int] = []  # papers in first-cited order
        self.keys = self._keys()
        self._assets: dict[int, Any] = {}

    def _keys(self) -> dict[int, str]:
        keys: dict[int, str] = {}
        seen: dict[str, int] = {}
        for pid in sorted(self.papers):
            base = bib_key(self.papers[pid])
            n = seen.get(base, 0)
            seen[base] = n + 1
            keys[pid] = base if n == 0 else f"{base}{chr(ord('a') + n - 1)}"
        return keys

    def cite(self, pid: int | None, locator: str = "") -> str:
        if pid is None or pid not in self.keys:
            return ""
        if pid not in self.cited:
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
        """Escaped text with its citations as ``[@key]``."""
        out: list[str] = []
        pos = 0
        for m in _CITATION.finditer(raw):
            out.append(_escape(raw[pos:m.start()]))
            out.append(self._citation(m))
            pos = m.end()
        out.append(_escape(raw[pos:]))
        return "".join(out)

    def inline(self, content: Any) -> str:
        out: list[str] = []
        for run in _runs(content):
            kind = run.get("type", "text")
            if kind == "link":
                label = self.inline(run.get("content")) or _escape(str(run.get("href", "")))
                out.append(f"[{label}](<{run.get('href', '')}>)")
            elif kind == "mention":
                props = run.get("props") or {}
                label = _escape(str(props.get("label") or ""))
                pid = props.get("paperId")
                key = self.cite(pid if isinstance(pid, int) else None)
                out.append(f"{label} [{key}]" if key else label)
            elif isinstance(run.get("text"), str):
                out.append(self._styled(run))
        return "".join(out)

    def _styled(self, run: dict[str, Any]) -> str:
        styles = run.get("styles") or {}
        if styles.get("code"):
            code = run["text"]
            fence = "``" if "`" in code else "`"
            return _wrap(code, fence + (" " if fence == "``" else ""),
                         (" " if fence == "``" else "") + fence)
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
        pid = b.props.get("paperContentId")
        if isinstance(pid, int):
            return pid
        if self.note.target_kind == "paper" and self.note.target_id.isdigit():
            return int(self.note.target_id)
        return None

    def source_line(self, b: Block, pid: int | None) -> str:
        source = _json(b.props.get("source"), {})
        parts: list[str] = []
        paper = self.papers.get(pid) if pid is not None else None
        if paper is not None:
            parts.append(_escape(paper.title))
        section = source.get("section") or b.section_key
        if isinstance(section, str) and section.strip():
            parts.append(_escape(section_label(section)))
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
                    return f"images/{b.id}{path.suffix.lower() or '.png'}", path.read_bytes()
            box = piece.get("box")
            page = piece.get("page")
            if isinstance(box, list) and len(box) == 4 and isinstance(page, int):
                pdf = page_pdf_path(paper.source_dir)
                frac = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
                png = crop_png(pdf, page, frac) if pdf else None
                if png:
                    return f"images/{b.id}.png", png
        except Exception:  # noqa: BLE001 - one missing image never fails the export
            logger.warning("note export: no image for block %s", b.id, exc_info=True)
        return None

    def figure(self, b: Block, pid: int | None, caption: str) -> list[str]:
        got = self.image(b, pid)
        cap = " ".join(caption.split())
        if got is None:
            return [p for p in (self.text(cap), IMAGE_UNAVAILABLE) if p]
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


def _block(w: _Writer, b: Block, heading_offset: int) -> str:
    t = b.type
    if t == "section":
        return f"## {w.text(str(b.props.get('title') or b.section_key or ''))}"
    if t == "heading":
        level = b.props.get("level")
        level = level if isinstance(level, int) else 1
        return f"{'#' * min(6, level + heading_offset)} {w.inline(b.content)}"
    if t == "divider":
        return "---"
    if t == "math":
        latex = "\n".join(ln for ln in str(b.props.get("latex") or "").splitlines()
                          if ln.strip())
        return f"$${latex}$$" if latex else ""
    if t == "codeBlock":
        code = "".join(str(r.get("text", "")) for r in _runs(b.content))
        code = code or str(b.props.get("code") or "")
        fence = "```"
        while fence in code:
            fence += "`"
        lang = str(b.props.get("language") or "").strip()
        return f"{fence}{lang}\n{code}\n{fence}"
    if t in ("quote", "callout"):
        return _quoted(w.inline(b.content).split("\n"))
    if t == "bulletListItem":
        return f"- {w.inline(b.content)}"
    if t == "numberedListItem":
        return f"1. {w.inline(b.content)}"
    if t == "checkListItem":
        return f"- [{'x' if b.props.get('checked') else ' '}] {w.inline(b.content)}"
    if t == "paperQuote":
        return _paper_quote(w, b)
    if t in ("paperFigure", "paperEquation", "paperRegion"):
        return _paper_media(w, b)
    text = w.inline(b.content)
    return _LINE_START.sub(r"\1\\\2", text)


def _paper_quote(w: _Writer, b: Block) -> str:
    pid = w.block_paper(b)
    live = live_pieces(_json(b.props.get("pieces"), []))
    quote = joined_quote(live) if live else ""
    quote = quote or str(b.props.get("quote") or "")
    lines = [w.text(ln) for ln in quote.split("\n")]
    lang = b.props.get("lang")
    if isinstance(lang, str) and lang.strip():
        label = canonical_language(lang)
        lines += ["", f"*Translated · {_escape(_LANG_SHORT.get(label, label))}*"]
        orig = b.props.get("orig")
        if isinstance(orig, str) and orig.strip():
            lines += ["", f"Original: {w.text(' '.join(orig.split()))}"]
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
            parts.append(f"$${' '.join(latex.split())}$$")
            caption = caption or latex
        got = w.image(b, pid)
        if got is not None:
            w.images[got[0]] = got[1]
            parts.append(f"![{w.text(' '.join(caption.split()))}]({got[0]})")
        elif not parts:
            parts += [w.text(caption), IMAGE_UNAVAILABLE]
    else:
        parts += w.figure(b, pid, caption)
    source = w.source_line(b, pid)
    if source:
        parts.append(source)
    return "\n\n".join(p for p in parts if p)


def note_to_markdown(
    note: Note, blocks: list[Block], papers: Mapping[int, ExportPaper], *,
    chunk_papers: Mapping[int, int] | None = None,
) -> tuple[str, dict[str, bytes], str]:
    """``(markdown, image files by relative path, BibTeX)`` of a note.
    ``blocks`` in document order; ``chunk_papers`` maps a cited chunk id to
    its paper."""
    w = _Writer(note, papers, chunk_papers or {})
    offset = 2 if any(b.type == "section" for b in blocks) else 1
    indent: dict[str, str] = {}
    types = {b.id: b.type for b in blocks}
    out = f"# {_escape(note.title)}"
    prev_marker: str | None = None
    for b in blocks:
        parent = b.parent_id
        pad = ""
        if parent in indent:
            pad = indent[parent] + ("    " if types.get(parent) in _LIST else "")
        indent[b.id] = pad
        body = _block(w, b, offset)
        if not body.strip():
            continue
        marker = _LIST_MARKER.get(b.type)
        body = "\n".join(pad + ln if ln else ln for ln in body.split("\n"))
        # Items of one list stay tight; a nested item continues its list.
        tight = marker is not None and (marker == prev_marker or bool(pad))
        out += ("\n" if tight else "\n\n") + body
        prev_marker = marker
    bib = "\n\n".join(_bib_entry(w.keys[pid], w.papers[pid]) for pid in w.cited)
    return out.rstrip() + "\n", w.images, (bib + "\n" if bib else "")


# --- loading ---------------------------------------------------------------

async def export_sources(
    conn: aiosqlite.Connection, note: Note, blocks: list[Block],
) -> tuple[dict[int, ExportPaper], dict[int, int]]:
    """The papers a note's export may cite (its own paper, mentioned and
    cited ones) and the paper of each chunk it cites."""
    pids, chunk_ids = _cited(blocks)
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
    return papers, chunk_papers


def slug(title: str) -> str:
    """A file-name slug of a note's title ("note" when nothing is left)."""
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60].rstrip("-") or "note"
