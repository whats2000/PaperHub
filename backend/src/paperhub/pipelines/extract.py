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


def _extract_pdf_metadata(pdf_path: Path) -> dict[str, object]:
    """Best-effort extraction of title/authors/year from PDF embedded metadata.

    Returns a dict with keys ``title`` (str), ``authors`` (list[str]), and
    ``year`` (int | None) — the same shape ``_ingest_upload`` already feeds
    into ``_persist_paper_content_and_chunks``. Keys are always present;
    values may be empty/None when metadata is missing or fails sanity checks.
    Callers should treat an empty ``title`` as a signal to fall back to a
    non-metadata default (e.g. filename stem).
    """
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        raw = doc.metadata or {}
    return {
        "title": _sanitise_pdf_title(raw.get("title")),
        "authors": _sanitise_pdf_authors(raw.get("author")),
        "year": _sanitise_pdf_year(
            raw.get("creationDate"), raw.get("modDate"),
        ),
    }
