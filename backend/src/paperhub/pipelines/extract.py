"""Extract paper text from LaTeX sources or PDFs.

LaTeX extraction adapted from paper2slides-plus/src/latex_utils.py:
- Identify the main .tex file (the one with \\begin{document}).
- Recursively inline \\input{...} and \\include{...}.
- Strip the preamble (everything before \\begin{document}).
- Return both the main path (for source_path persistence) and the flattened
  body text (for chunking).

PDF extraction uses PyMuPDF's plain-text export.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pymupdf
from pylatexenc.latex2text import LatexNodes2Text

from paperhub.pipelines.section_names import is_heading_shaped, is_wrapped_continuation

logger = logging.getLogger(__name__)

# Match the REAL \begin{document} / \end{document} markers — skip any
# occurrence inside a LaTeX %-comment. Tripped by IEEE journal classes
# (arXiv:2503.07137) that ship template stubs like:
#     %\begin{document}
#     % \title{Example}
#     %\end{document}
# in the preamble. The old substring regex hit the commented \begin first,
# sliced the preamble at that point, and kept the real \begin{document} in
# the body — pandoc then saw an opening with no closing match and the
# renderer fell back to the pylatexenc <pre> view (plain-text appearance).
#
# Pattern: from start-of-line (re.M), allow any chars that are not an
# unescaped %, then the literal command. The literal is captured as group 1
# so callers can use ``m.start(1)`` / ``m.end(1)`` to slice exactly at the
# command (NOT at the start of the line — that would swallow body content
# when the document is on a single line).
_BEGIN_DOC = re.compile(r"(?m)^(?:[^%\n]|\\%)*(\\begin\{document\})")
_END_DOC = re.compile(r"(?m)^(?:[^%\n]|\\%)*(\\end\{document\})")
_INPUT_INCLUDE = re.compile(r"\\(?:input|include)\{([^}]+)\}")

# Signals used to score which .tex file is the paper's root document (see
# _main_tex_score). All ignore commented-out lines, same as _BEGIN_DOC.
_DOCUMENTCLASS = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\documentclass")
_TITLE_CMD = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\title\{")
_MAKETITLE = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\maketitle")
_SECTION_CMD = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\(?:section|chapter)\b")
# Common root filenames; a tarball's true main is very often one of these.
_PREFERRED_MAIN_STEMS = frozenset(
    {"main", "paper", "ms", "manuscript", "root", "article", "arxiv"}
)

# Stock placeholder titles emitted by Word, OpenOffice, InDesign etc. when the
# author never set a real title — anything here gets treated as "no title".
_STOCK_PLACEHOLDER_TITLE = re.compile(
    r"^(untitled|microsoft word - .*|layout \d+|document\d*|presentation\d*)$",
    re.IGNORECASE,
)
# PDF date format per the PDF spec: ``D:YYYYMMDDHHMMSS+HH'mm'``.
_PDF_DATE_YEAR = re.compile(r"D:(\d{4})")
# Split author strings on ``;`` or the word ``and`` — NOT on ``,`` because
# academic author lists routinely embed commas inside a single name
# ("Smith, A; Lee, B"), and splitting on ``,`` would shred those.
_AUTHOR_SPLIT = re.compile(r"\s*(?:;|\band\b)\s*", re.IGNORECASE)
# Collapses runs of whitespace into a single space when stitching page-1
# spans back together for the largest-font title heuristic.
_WHITESPACE_RUN = re.compile(r"\s+")
# Article-type banners that sit above the title on publisher first pages.
_BANNER_WORD = re.compile(
    r"^(open|open access|article|research article|original article|resource|"
    r"review|review article|letter|communication|brief report|research)$",
    re.IGNORECASE,
)
# A page-1 font group below the largest must be this much bigger than body
# text to count as a title (author lines / headings sit at 1.0-1.3x body).
_TITLE_MIN_BODY_RATIO = 1.4
# U+FFFD and C0/C1 control characters: a mis-decoded string, never a title.
_GARBLED_CHAR = re.compile(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class LatexExtract:
    main_path: Path
    flattened_text: str
    # Everything before \begin{document} (with \input chains already inlined) —
    # the macro definitions the body strips out. Fed to the Citation Canvas
    # renderer so author macros (\vx, \Ls, …) reach MathJax defined.
    preamble: str = ""


def _main_tex_score(path: Path, text: str) -> float:
    """Heuristic score for how likely a ``.tex`` file is the paper's root.

    arXiv tarballs frequently ship table/figure files that are *independently
    compilable* — same ``\\documentclass`` + ``\\begin{document}`` as the real
    paper — e.g. arXiv:2406.07524 shipped ``gb_results_table.tex`` (two tables)
    next to the 160 KB ``main.tex``. First-match-wins then picked whichever
    sorted first (``gb_...`` < ``main``), so the Citation Canvas rendered only
    the tables. The real root almost always carries a ``\\title`` +
    ``\\maketitle`` + many ``\\section`` commands and is large; a fragment has
    none of those. Higher = more likely the true main file. All signals ignore
    commented-out lines, same as ``_BEGIN_DOC``."""
    score = 0.0
    if _DOCUMENTCLASS.search(text):
        score += 5.0
    if _TITLE_CMD.search(text):
        score += 10.0
    if _MAKETITLE.search(text):
        score += 5.0
    score += min(len(_SECTION_CMD.findall(text)), 10)
    if path.stem.lower() in _PREFERRED_MAIN_STEMS:
        score += 8.0
    # Size as a minor tiebreak — the real paper dwarfs a fragment.
    score += len(text) / 50_000.0
    return score


def _find_main_tex(source_dir: Path) -> Path:
    candidates = sorted(source_dir.glob("*.tex"))  # deterministic tie order
    if not candidates:
        raise FileNotFoundError(f"no .tex files in {source_dir}")
    texts = {c: c.read_text(encoding="utf-8", errors="ignore") for c in candidates}
    # Prefer files that actually open a document body; among those (or all .tex
    # if none do) pick the highest-scoring root. `max` returns the first on a
    # tie, and `candidates` is sorted, so ties break alphabetically.
    roots = [c for c in candidates if _BEGIN_DOC.search(texts[c])]
    pool = roots or candidates
    return max(pool, key=lambda c: _main_tex_score(c, texts[c]))


def _inline_recursive(text: str, root: Path, seen: set[Path]) -> str:
    def repl(m: re.Match[str]) -> str:
        rel = m.group(1).strip()
        if not rel.endswith(".tex"):
            rel = rel + ".tex"
        target = (root / rel).resolve()
        if target in seen:
            return ""
        if not target.exists():
            # Make missing inputs visible — silently swallowing them in the
            # past hid the tarball-flatten bug where `\input{sections/foo}`
            # pointed at files that had been re-rooted by the extractor.
            logger.warning(
                "extract: missing \\input/\\include target %r (looked for %s)",
                rel, target,
            )
            return ""
        seen.add(target)
        inner = target.read_text(encoding="utf-8", errors="ignore")
        return _inline_recursive(inner, root, seen)

    return _INPUT_INCLUDE.sub(repl, text)


def extract_latex(source_dir: Path) -> LatexExtract:
    """Extract flattened body text from a LaTeX source directory.

    Strips the preamble (everything before and including ``\\begin{document}``)
    and the closing ``\\end{document}`` tag.  All ``\\input`` / ``\\include``
    directives are inlined recursively.
    """
    main = _find_main_tex(source_dir)
    raw = main.read_text(encoding="utf-8", errors="ignore")
    flat = _inline_recursive(raw, source_dir, seen={main.resolve()})
    # Strip preamble (everything up to and including \\begin{document}) — but
    # keep it: the macro definitions there are needed by the Canvas renderer.
    # Group 1 is the literal \\begin{document} / \\end{document}; group 0 also
    # consumes the (comment-free) prefix on the same line, so we MUST slice
    # against group 1 to avoid eating real content.
    begin_m = _BEGIN_DOC.search(flat)
    preamble = flat[: begin_m.start(1)] if begin_m else ""
    if begin_m:
        flat = flat[begin_m.end(1):]
    end_m = _END_DOC.search(flat)
    if end_m:
        flat = flat[: end_m.start(1)]
    return LatexExtract(
        main_path=main, flattened_text=flat.strip(), preamble=preamble,
    )


def extract_pdf(pdf_path: Path) -> str:
    """Return concatenated plain text from a PDF, one form-feed-separated
    page per source page.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    pieces: list[str] = []
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        for page in doc:
            pieces.append(page.get_text("text"))
    return "\n\f\n".join(pieces).strip()


# Page separator used when concatenating per-page text. MUST match the join
# in ``extract_pdf`` so heading char offsets from ``extract_pdf_with_headings``
# align with the full_text it returns.
_PDF_PAGE_SEP = "\n\f\n"
# A line whose font size exceeds the modal body size by at least this many
# points (while staying below the page-1 title size) is treated as a section
# heading. 1.0pt clears rounding noise while catching the typical body(10-11)
# vs heading(12-14) gap.
_HEADING_MIN_DELTA = 1.0
# Headings are short lines; longer lines are prose that merely happens to be
# emphasised. Caps a runaway large-font paragraph from becoming a "section".
_HEADING_MAX_CHARS = 120
# A candidate must have at least this many chars and at least one letter, so
# page numbers ("1"), stray glyphs, and rule lines don't become "sections".
_HEADING_MIN_CHARS = 3
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _looks_like_heading(name: str) -> bool:
    """Reject non-heading-shaped lines that happen to share the heading font:
    too-short / letter-less strings (page numbers, glyphs), URLs (journal
    footers), and lines with control characters (extraction garbage)."""
    if len(name) < _HEADING_MIN_CHARS:
        return False
    if not _HAS_LETTER.search(name):
        return False
    if "www." in name or "http" in name:
        return False
    return name.isprintable()


def _modal_body_font_size(doc: pymupdf.Document) -> float:
    """Most-common (weighted by character count) rounded span font size.

    This is the body-text size: headings sit above it, captions/footnotes
    below. Returns ``0.0`` for an empty / image-only document.
    """
    weight: dict[float, int] = defaultdict(int)
    for pno in range(len(doc)):
        blocks = doc[pno].get_text("dict").get("blocks", [])  # type: ignore[no-untyped-call]
        for b in blocks:
            if b.get("type", 0) != 0:
                continue
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    sz = round(span.get("size", 0.0), 1)
                    txt = (span.get("text") or "").strip()
                    if sz > 0 and txt:
                        weight[sz] += len(txt)
    if not weight:
        return 0.0
    return max(weight, key=lambda s: weight[s])


def _page1_title_size(doc: pymupdf.Document) -> float:
    """Largest span font size on page 1 — the title size, which we exclude
    from the heading band so the paper title isn't treated as a section."""
    if len(doc) == 0:
        return 0.0
    blocks = doc[0].get_text("dict").get("blocks", [])  # type: ignore[no-untyped-call]
    mx = 0.0
    for b in blocks:
        if b.get("type", 0) != 0:
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                if (span.get("text") or "").strip():
                    mx = max(mx, round(span.get("size", 0.0), 1))
    return mx


def extract_pdf_with_headings(
    pdf_path: Path,
) -> tuple[str, list[tuple[str, int]]]:
    """Return ``(full_text, headings)`` for a PDF.

    ``full_text`` is the same per-page concatenation as :func:`extract_pdf`
    (NOT stripped, so the heading char offsets stay exact). ``headings`` is a
    list of ``(name, char_offset)`` where each name is a line whose font size
    lands in the band ``[modal_body + 1pt, page1_title)`` and is short enough
    to be a heading rather than emphasised prose. Offsets index into
    ``full_text``. Returns ``[]`` headings for flat single-font / image-only
    PDFs — the caller then falls back to a single synthetic section.

    Used by the Paper Pipeline's PDF branches so ``kind='pdf_upload'`` papers
    get a real ``sections_json`` and the paper_qa subagent can navigate them
    by section (mirrors the LaTeX ``\\section{}`` path).
    """
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    pieces: list[str] = []
    # Collect raw candidates first, then drop running headers (banners that
    # repeat across pages — "Article", journal name, footer) in a second pass.
    # (name, offset, set entirely in the heading font): see the naming pass.
    candidates: list[tuple[str, int, bool]] = []
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        body_size = _modal_body_font_size(doc)
        title_size = _page1_title_size(doc)
        offset = 0
        last = len(doc) - 1
        for pno, page in enumerate(doc):
            page_text: str = page.get_text("text")
            blocks = page.get_text("dict").get("blocks", [])
            for b in blocks:
                if b.get("type", 0) != 0:
                    continue
                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    name = "".join(s.get("text") or "" for s in spans).strip()
                    if len(name) > _HEADING_MAX_CHARS or not _looks_like_heading(name):
                        continue
                    sizes = [
                        round(s.get("size", 0.0), 1) for s in spans
                        if (s.get("text") or "").strip()
                    ]
                    size = max(sizes, default=0.0)
                    in_band = size >= body_size + _HEADING_MIN_DELTA and (
                        title_size <= 0.0 or size < title_size
                    )
                    if not in_band:
                        continue
                    # A real heading is set ENTIRELY in the heading font; a
                    # prose line with one larger inline span (`conv_block` in
                    # code font) only reaches the band through that span.
                    uniform = min(sizes) >= body_size + _HEADING_MIN_DELTA
                    idx = page_text.find(name)
                    if idx >= 0:
                        candidates.append((name, offset + idx, uniform))
            pieces.append(page_text)
            offset += len(page_text)
            if pno < last:
                offset += len(_PDF_PAGE_SEP)
    # Running headers repeat on multiple pages; a real section heading appears
    # once. Drop every name that occurs more than once.
    name_counts: dict[str, int] = defaultdict(int)
    for name, _, _ in candidates:
        name_counts[name] += 1
    full_text = _PDF_PAGE_SEP.join(pieces)
    return full_text, _name_heading_candidates(
        [c for c in candidates if name_counts[c[0]] == 1], full_text,
    )


def _name_heading_candidates(
    candidates: list[tuple[str, int, bool]], full_text: str,
) -> list[tuple[str, int]]:
    """Name each font-size candidate boundary.

    A real heading is set entirely in the heading font AND is heading-shaped.
    Any other candidate (a code line, a prose line with a larger inline span)
    KEEPS its boundary — chunk offsets, and so every stored chunk id /
    citation, stay stable across re-ingest — but takes the name of the real
    heading it sits under. The lowercase second line of a wrapped heading,
    directly after it, is joined onto that heading. A candidate before any real
    heading has nothing to inherit and is dropped (its text joins the unnamed
    front matter).
    """
    out: list[tuple[str, int]] = []
    current: str | None = None
    current_idx = -1
    prev_end: int | None = None  # end of the previous candidate if it was a heading
    for name, off, uniform in candidates:
        follows_heading = (
            prev_end is not None and not full_text[prev_end:off].strip()
        )
        if uniform and is_heading_shaped(name):
            current = name
            current_idx = len(out)
            out.append((name, off))
            prev_end = off + len(name)
        elif (
            uniform and follows_heading and current is not None
            and is_wrapped_continuation(name)
        ):
            current = f"{current} {name}"
            out[current_idx] = (current, out[current_idx][1])
            out.append((current, off))
            prev_end = off + len(name)
        else:
            if current is not None:
                out.append((current, off))
            prev_end = None
    return out


def extract_pdf_page1_text(pdf_path: Path) -> str:
    """Return plain text from page 1 of a PDF, or ``""`` if the doc is empty.

    Used by the LLM-based title-extraction fallback in
    ``paperhub.pipelines.title_extract`` — the model needs just page 1
    (where titles, authors, and the abstract live), not the whole paper.
    Same ``with pymupdf.open(...) as doc:`` resource-management pattern
    as ``extract_pdf`` to keep PDF handles deterministic on Windows.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        if len(doc) == 0:
            return ""
        text: str = doc[0].get_text("text")
        return text


def _sanitise_pdf_title(raw: object) -> str:
    """Return a trimmed title or an empty string if the value is junk.

    Rejects whitespace-only values, values longer than 500 chars (pathological
    metadata), and the stock placeholder strings producers emit when no title
    was set (Word's ``Microsoft Word - foo.docx``, InDesign's ``Layout 1``,
    etc.).
    """
    if not isinstance(raw, str):
        return ""
    t = raw.strip()
    if not t or len(t) > 500:
        return ""
    if _STOCK_PLACEHOLDER_TITLE.match(t):
        return ""
    if _GARBLED_CHAR.search(t):
        return ""
    return t


def _sanitise_pdf_authors(raw: object) -> list[str]:
    """Split a PDF ``author`` string on ``;``/``,``/`and` and drop junk.

    Drops empties and items longer than 100 chars (typically concatenated
    affiliations).
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    parts = [p.strip() for p in _AUTHOR_SPLIT.split(raw) if p.strip()]
    return [p for p in parts if 0 < len(p) <= 100]


def _sanitise_pdf_year(creation: object, mod: object) -> int | None:
    """Extract a 4-digit year from PDF creation/mod dates with sanity check.

    Prefers ``creationDate`` over ``modDate``. Returns ``None`` for years
    outside ``1990..(current year + 1)`` — anything earlier is bogus PDF
    metadata; anything later is clock skew or fake.
    """
    upper = date.today().year + 1
    for raw in (creation, mod):
        if not isinstance(raw, str):
            continue
        m = _PDF_DATE_YEAR.match(raw)
        if not m:
            continue
        try:
            y = int(m.group(1))
        except ValueError:
            continue
        if 1990 <= y <= upper:
            return y
    return None


def page1_title_groups(doc: pymupdf.Document) -> tuple[list[tuple[float, str]], float]:
    """Page-1 text grouped by font size, largest first, plus the body size.

    Each group is ``(size, text)`` with the spans at that size joined in
    document order (blocks, then lines, then spans). The body size is the
    size carrying the most characters. Returns ``([], 0.0)`` for an empty or
    image-only page.
    """
    if len(doc) == 0:
        return [], 0.0
    blocks = doc[0].get_text("dict").get("blocks", [])  # type: ignore[no-untyped-call]
    pieces: dict[float, list[str]] = defaultdict(list)
    chars: dict[float, int] = defaultdict(int)
    for b in blocks:
        if b.get("type", 0) != 0:  # 0 = text; skip image blocks.
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                sz = round(span.get("size", 0.0), 1)
                txt = span.get("text", "")
                if sz <= 0.0 or not txt:
                    continue
                pieces[sz].append(txt)
                chars[sz] += len(txt.strip())
    if not pieces:
        return [], 0.0
    body = max(chars, key=lambda k: chars[k])
    groups = [
        (sz, _WHITESPACE_RUN.sub(" ", " ".join(pieces[sz])).strip())
        for sz in sorted(pieces, reverse=True)
    ]
    return groups, body


def looks_like_title_text(text: str) -> bool:
    """False for page-1 text that outsizes the title without being one: a
    decorative drop cap (``Q``), an all-lowercase journal wordmark
    (``applied sciences``) or an article-type banner (``Resource``)."""
    if sum(c.isalpha() for c in text) < 4:
        return False
    if not any(c.isupper() for c in text):
        return False
    return _BANNER_WORD.match(text) is None


def _extract_pdf_title_from_page1_doc(doc: pymupdf.Document) -> str:
    """Heuristic: title is the largest-font text on page 1 that reads as one.

    Works for the typical academic-paper layout (title prominent at the top
    of page 1, larger than abstract / authors / affiliations / journal
    citation). Font-size groups are tried largest first; a drop cap, a
    lowercase journal wordmark or an article-type banner is skipped
    (:func:`looks_like_title_text`). A group below the largest must also
    stand clearly above body text (``_TITLE_MIN_BODY_RATIO``) so the walk
    never lands on an author line or a section heading. Returns ``""`` when
    no group qualifies; the caller then tries the LLM / filename stem.

    This is the second-tier fallback used when ``doc.metadata['title']`` is
    empty or junk — Adobe InDesign / Word PDFs from Nature, Springer, etc.
    routinely ship with metadata fields stripped even when the rendered
    page-1 title is right there at 24-26pt.
    """
    groups, body = page1_title_groups(doc)
    for i, (size, text) in enumerate(groups):
        if i > 0 and size < body * _TITLE_MIN_BODY_RATIO:
            break
        if looks_like_title_text(text):
            return _sanitise_pdf_title(text)
    return ""


def legacy_page1_title(doc: pymupdf.Document) -> str:
    """The pre-fix page-1 pick (plain largest-font text). Used only by the
    title-repair CLI to tell an auto-derived stored title from a typed one."""
    groups, _ = page1_title_groups(doc)
    return _sanitise_pdf_title(groups[0][1]) if groups else ""


# First uncommented ``\title{`` (optionally ``\title[short]{``). Requires the
# opening brace, so ``\titlepage`` / ``\titlefont`` never match.
_TITLE_START = re.compile(r"(?m)^(?:[^%\n]|\\%)*?\\title\s*(?:\[[^\]]*\])?\s*\{")
# Title-attached notes whose argument is NOT part of the title text.
_TITLE_NOTE_CMD = re.compile(r"\\(?:thanks|footnote|footnotetext)\s*\{")
# Forced line break inside a title: ``\\`` or ``\\[2pt]``.
_TITLE_LINEBREAK = re.compile(r"\\\\(?:\[[^\]]*\])?")


def _balanced_close(text: str, open_idx: int) -> int:
    """Index of the ``}`` closing the ``{`` at ``open_idx``, or -1 if unbalanced.
    Escaped braces (``\\{`` / ``\\}``) do not count."""
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_latex_title(tex: str) -> str:
    """Plain-text paper title from the first ``\\title{...}`` in ``tex``.

    The source-derived fallback for arXiv ingest when the arXiv metadata API
    is unavailable (e.g. HTTP 429 rate limiting). Drops ``\\thanks`` /
    ``\\footnote`` arguments, turns ``\\\\`` line breaks into spaces, and
    converts remaining markup to text via pylatexenc. Returns ``""`` when no
    usable title is found.
    """
    m = _TITLE_START.search(tex)
    if m is None:
        return ""
    open_idx = m.end() - 1
    close_idx = _balanced_close(tex, open_idx)
    if close_idx < 0:
        return ""
    inner = tex[open_idx + 1 : close_idx]
    while (note := _TITLE_NOTE_CMD.search(inner)) is not None:
        end = _balanced_close(inner, note.end() - 1)
        inner = inner[: note.start()] + ("" if end < 0 else inner[end + 1 :])
        if end < 0:
            break
    inner = _TITLE_LINEBREAK.sub(" ", inner)
    try:
        text = LatexNodes2Text().latex_to_text(inner)
    except Exception:  # noqa: BLE001 — malformed markup: best-effort fallback
        text = inner
    return _sanitise_pdf_title(_WHITESPACE_RUN.sub(" ", text))


def _extract_pdf_title_from_page1(pdf_path: Path) -> str:
    """Thin wrapper around ``_extract_pdf_title_from_page1_doc`` that opens
    the PDF and delegates — exists for unit-testability without forcing
    tests to manage the ``pymupdf.Document`` lifecycle.
    """
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        return _extract_pdf_title_from_page1_doc(doc)


def _extract_pdf_metadata(pdf_path: Path) -> dict[str, object]:
    """Best-effort extraction of title/authors/year from PDF embedded metadata.

    Returns a dict with keys ``title`` (str), ``authors`` (list[str]), and
    ``year`` (int | None) — the same shape ``_ingest_upload`` already feeds
    into ``_persist_paper_content_and_chunks``. Keys are always present;
    values may be empty/None when metadata is missing or fails sanity checks.
    Callers should treat an empty ``title`` as a signal to fall back to a
    non-metadata default (e.g. filename stem).

    The ``title`` field uses a two-tier fallback: first ``doc.metadata['title']``
    (after sanitisation), then — when that's empty — the page-1 largest-font
    heuristic. Publisher PDFs from InDesign / Word commonly ship with the
    metadata title stripped even when page 1 carries the title at 24-26pt.
    """
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        raw = doc.metadata or {}
        meta_title = _sanitise_pdf_title(raw.get("title"))
        # Fall back to the page-1 largest-font heuristic ONLY when the
        # embedded metadata title is unusable. Pass the already-open doc
        # through to avoid a second pymupdf.open() round-trip.
        title = meta_title or _extract_pdf_title_from_page1_doc(doc)
        authors = _sanitise_pdf_authors(raw.get("author"))
        year = _sanitise_pdf_year(
            raw.get("creationDate"), raw.get("modDate"),
        )
    return {
        "title": title,
        "authors": authors,
        "year": year,
    }
