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

logger = logging.getLogger(__name__)

_BEGIN_DOC = re.compile(r"\\begin\{document\}")
_END_DOC = re.compile(r"\\end\{document\}")
_INPUT_INCLUDE = re.compile(r"\\(?:input|include)\{([^}]+)\}")

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


@dataclass(frozen=True)
class LatexExtract:
    main_path: Path
    flattened_text: str


def _find_main_tex(source_dir: Path) -> Path:
    candidates = list(source_dir.glob("*.tex"))
    if not candidates:
        raise FileNotFoundError(f"no .tex files in {source_dir}")
    for cand in candidates:
        text = cand.read_text(encoding="utf-8", errors="ignore")
        if _BEGIN_DOC.search(text):
            return cand
    # Fallback: first .tex.
    return candidates[0]


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
    # Strip preamble (everything up to and including \\begin{document}).
    begin_m = _BEGIN_DOC.search(flat)
    if begin_m:
        flat = flat[begin_m.end():]
    end_m = _END_DOC.search(flat)
    if end_m:
        flat = flat[: end_m.start()]
    return LatexExtract(main_path=main, flattened_text=flat.strip())


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
    candidates: list[tuple[str, int]] = []
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
                    size = max(
                        (round(s.get("size", 0.0), 1) for s in spans
                         if (s.get("text") or "").strip()),
                        default=0.0,
                    )
                    in_band = size >= body_size + _HEADING_MIN_DELTA and (
                        title_size <= 0.0 or size < title_size
                    )
                    if not in_band:
                        continue
                    idx = page_text.find(name)
                    if idx >= 0:
                        candidates.append((name, offset + idx))
            pieces.append(page_text)
            offset += len(page_text)
            if pno < last:
                offset += len(_PDF_PAGE_SEP)
    # Running headers repeat on multiple pages; a real section heading appears
    # once. Drop every name that occurs more than once.
    name_counts: dict[str, int] = defaultdict(int)
    for name, _ in candidates:
        name_counts[name] += 1
    headings = [(n, o) for n, o in candidates if name_counts[n] == 1]
    return _PDF_PAGE_SEP.join(pieces), headings


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


def _extract_pdf_title_from_page1_doc(doc: pymupdf.Document) -> str:
    """Heuristic: title is the text at the largest font size on page 1.

    Works for the typical academic-paper layout (title prominent at the top
    of page 1, larger than abstract / authors / affiliations / journal
    citation). Returns ``""`` when page 1 is empty, image-only, or the
    largest-font text fails to sanitise into a plausible title.

    This is the second-tier fallback used when ``doc.metadata['title']`` is
    empty or junk — Adobe InDesign / Word PDFs from Nature, Springer, etc.
    routinely ship with metadata fields stripped even when the rendered
    page-1 title is right there at 24-26pt.
    """
    if len(doc) == 0:
        return ""
    page = doc[0]
    blocks = page.get_text("dict").get("blocks", [])  # type: ignore[no-untyped-call]
    # First pass: find the maximum text-span font size on page 1.
    max_size = 0.0
    for b in blocks:
        if b.get("type", 0) != 0:  # 0 = text; skip image blocks.
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                sz = round(span.get("size", 0.0), 1)
                if sz > max_size:
                    max_size = sz
    if max_size <= 0.0:
        return ""
    # Second pass: collect every span at the max size in document order
    # (blocks, then lines within each block, then spans within each line).
    pieces: list[str] = []
    for b in blocks:
        if b.get("type", 0) != 0:
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                if round(span.get("size", 0.0), 1) == max_size:
                    txt = span.get("text", "")
                    if txt:
                        pieces.append(txt)
    joined = " ".join(pieces)
    cleaned = _WHITESPACE_RUN.sub(" ", joined).strip()
    return _sanitise_pdf_title(cleaned)


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
