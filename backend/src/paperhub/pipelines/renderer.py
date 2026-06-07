"""Render paper source to HTML for the Citation Canvas (FR-03).

Strategy:
- LaTeX: pandoc primary (good math + figure support). pylatexenc fallback
  when pandoc is absent OR exits non-zero (idiosyncratic LaTeX is common).
- PDF: PyMuPDF's HTML export (preserves layout enough for highlight scrolling).
"""
from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import pymupdf
from pylatexenc.latex2text import LatexNodes2Text

from paperhub.pipelines.mathjax_macros import (
    MacroValue,
    build_mathjax_config_script,
)

logger = logging.getLogger(__name__)

# pandoc --mathjax injects a bare MathJax loader <script> with no inline config.
# We splice our window.MathJax config in just before it. Matches the opening
# <script ...> whose attributes (which may span newlines, hence [^>]) reference
# mathjax — the loader tag, not the polyfill above it.
_MATHJAX_SCRIPT_RE = re.compile(r"<script\b(?=[^>]*?mathjax)")

_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)
# Inline base64 <img> emitted by PyMuPDF's get_text("html") for PDF pages.
_DATA_URI_IMG_RE = re.compile(
    r'(<img\b[^>]*?\bsrc=")data:image/([a-zA-Z0-9.+-]+);base64,([^"]+)(")',
    re.IGNORECASE,
)
_IMG_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
# data: URI subtype -> on-disk extension for extracted PDF page images.
_DATA_URI_EXT = {
    "png": ".png",
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "gif": ".gif",
    "svg+xml": ".svg",
    "webp": ".webp",
}

# pandoc can HANG (not just exit non-zero) on pathological LaTeX. Without a
# bound the hanging subprocess parks the whole ingest until the worker OOMs
# (arxiv:2410.12557 reproduced this). Cap it and treat a timeout like any
# other pandoc failure — fall back to pylatexenc, then the raw envelope.
_PANDOC_TIMEOUT_SECONDS = 60

# Max stray unclosed braces we'll delete before retrying pandoc. A real-world
# typo leaves 1 (arXiv:2406.07524 ships `\owt{` with no close); a large count
# signals something else (e.g. a verbatim miscount) where blind editing is
# unlikely to help, so we don't bother and fall back instead.
_MAX_BRACE_FIX = 16


def _unmatched_open_braces(text: str) -> list[int]:
    """Indices of unmatched opening ``{`` (escape- + comment-aware).

    pdflatex tolerates a stray unclosed brace — a common authoring typo
    (arXiv:2406.07524 ships ``\\owt{`` with no close) — by implicitly closing
    the group at ``\\end{document}``; pandoc's stricter parser instead rejects
    the whole document with "unexpected end of input". Returning the exact
    positions lets us delete the typo'd opener and retry pandoc.

    We delete the opener rather than append a closer at EOF: a trailing ``}``
    makes the stray ``{`` swallow the entire remainder of the paper into the
    preceding macro's argument (so pandoc renders only the prefix), whereas
    deleting the typo'd ``{`` renders the full document.
    """
    stack: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:  # escaped char (\{ \} \%) — skip both
            i += 2
            continue
        if c == "%":  # unescaped comment — skip to end of line
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            continue
        if c == "{":
            stack.append(i)
        elif c == "}" and stack:
            stack.pop()
        i += 1
    return stack


def _unclosed_braces(text: str) -> int:
    """Count of unmatched opening braces (see :func:`_unmatched_open_braces`)."""
    return len(_unmatched_open_braces(text))


def render_html(
    *,
    source: Path,
    kind: Literal["latex", "pdf"],
    out_path: Path,
    resource_dir: Path | None = None,
    macros: dict[str, MacroValue] | None = None,
) -> Path:
    """Render ``source`` to an HTML artefact at ``out_path``.

    ``resource_dir`` (latex only) is where figures referenced by the flattened
    source actually live — typically the extracted ``source/`` subtree, a
    different directory from the flattened ``.tex``. pandoc searches it via
    ``--resource-path``; figure ``<img>`` refs are then rewritten by
    ``_externalize_local_images`` to relative ``asset/`` URLs the Citation
    Canvas iframe resolves back to the backend (serving each figure lazily as a
    file). We deliberately do NOT base64-inline figures: a paper with 70MB of
    figures produced a 70MB HTML that OOM'd the iframe (arxiv:2605.02881). Math
    is rendered via an EXTERNAL MathJax CDN ``<script>`` (``--mathjax``, not
    ``--embed-resources``) so multi-line environments like ``\\begin{aligned}``
    render in the browser without fetching+inlining ~1.3MB of MathJax per paper.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "pdf":
        _render_pdf(source, out_path)
    elif kind == "latex":
        if shutil.which("pandoc"):
            try:
                _render_latex_pandoc(source, out_path, resource_dir=resource_dir)
                if resource_dir is not None:
                    _externalize_local_images(out_path, resource_dir)
                # Feed MathJax the paper's macro definitions + curated package
                # macros so \vx, \Ls, \mathbbm, … render instead of leaking raw.
                _inject_mathjax_macros(out_path, macros)
                return out_path
            except subprocess.CalledProcessError as exc:
                # Idiosyncratic LaTeX commonly trips pandoc with non-zero exit.
                # Fall back to pylatexenc so the upstream pipeline (which has
                # already spent significant work on download + extract) still
                # produces a usable HTML artefact for the Citation Canvas.
                logger.warning(
                    "pandoc failed on %s (exit %s); falling back to pylatexenc. "
                    "stderr: %s",
                    source,
                    exc.returncode,
                    (exc.stderr or "")[:500],
                )
                # A stray unclosed brace (author typo pdflatex tolerates) makes
                # pandoc reject the whole document. Re-balance and retry once
                # before degrading to the plain-text fallback.
                if _try_pandoc_brace_balanced(
                    source, out_path, resource_dir=resource_dir, macros=macros,
                ):
                    return out_path
            except subprocess.TimeoutExpired:
                # pandoc hung past the cap — kill it and fall back rather than
                # parking the ingest until the worker OOMs.
                logger.warning(
                    "pandoc timed out after %ss on %s; falling back to pylatexenc.",
                    _PANDOC_TIMEOUT_SECONDS,
                    source,
                )
        # pandoc absent OR exited non-zero — try pylatexenc.
        try:
            _render_latex_pylatexenc(source, out_path)
            return out_path
        except Exception as exc:  # noqa: BLE001 — pylatexenc raises bare Exception subclasses on hostile LaTeX
            logger.warning(
                "pylatexenc failed on %s (%s: %s); falling back to raw-text envelope.",
                source,
                type(exc).__name__,
                str(exc)[:200],
            )
            _render_latex_raw_envelope(source, out_path)
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    return out_path


def _render_pdf(pdf_path: Path, out_path: Path) -> None:
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        pieces = ["<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"]
        for page in doc:
            pieces.append("<div class='page'>")
            pieces.append(page.get_text("html"))
            pieces.append("</div>")
        pieces.append("</body></html>")
    # PyMuPDF inlines every page image as a base64 data: URI — the PDF-render
    # counterpart of the figure-bloat that OOM'd the canvas. Extract each to a
    # file and rewrite to a relative asset/ URL served lazily by the backend.
    html = _externalize_data_uri_images(
        "".join(pieces),
        out_dir=out_path.parent / "pdf_assets",
        html_dir=out_path.parent,
    )
    out_path.write_text(html, encoding="utf-8")


def _render_latex_pandoc(
    tex_path: Path, out_path: Path, *, resource_dir: Path | None = None,
) -> None:
    cmd = [
        "pandoc",
        "--from", "latex",
        "--to", "html5",
        "--standalone",
        # Render math via an external MathJax CDN <script>. pandoc's built-in
        # conversion can't handle multi-line math (\begin{aligned}, $$..$$) and
        # dumps raw TeX; MathJax renders it in the browser. We deliberately do
        # NOT use --embed-resources: it would fetch + inline ~1.3MB of MathJax
        # into every paper at ingest (~12s/paper + network dependency). Figure
        # <img> refs are rewritten to served asset/ URLs by
        # _externalize_local_images (NOT base64-inlined — that OOM'd the canvas).
        "--mathjax",
        # Preserve the source's line breaks instead of reflowing at ~72 cols.
        # Default wrapping splits a long line inside math, and a `%` LaTeX
        # comment line (e.g. arXiv:1706.03762's commented MultiHead `where`
        # row) then only comments its FIRST wrapped fragment — the remainder
        # (here an invalid double-subscript `QW_Q_i`) becomes live math and
        # breaks the render. Preserving line breaks keeps each `%` comment on
        # its own line, fully commented.
        "--wrap=preserve",
    ]
    if resource_dir is not None:
        # Figures live in the extracted source/ subtree, not next to the
        # flattened .tex — tell pandoc where to find them for embedding.
        cmd += ["--resource-path", str(resource_dir)]
    cmd += [str(tex_path), "-o", str(out_path)]
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        cwd=tex_path.parent,
        timeout=_PANDOC_TIMEOUT_SECONDS,
    )


def _try_pandoc_brace_balanced(
    source: Path,
    out_path: Path,
    *,
    resource_dir: Path | None,
    macros: dict[str, MacroValue] | None,
) -> bool:
    """Retry pandoc once after deleting any stray unclosed ``{`` (author typo).

    Returns True iff the repaired source rendered. The temp copy lives beside
    ``source`` so its ``\\input`` + relative figure paths still resolve;
    sentinels are preserved (we only delete the typo'd brace character), so
    chunk anchoring survives the retry.
    """
    text = source.read_text(encoding="utf-8", errors="ignore")
    positions = _unmatched_open_braces(text)
    if not 0 < len(positions) <= _MAX_BRACE_FIX:
        return False
    chars = list(text)
    for p in sorted(positions, reverse=True):  # reverse so earlier idx stay valid
        del chars[p]
    repaired = "".join(chars)
    balanced = source.with_name(source.stem + ".balanced.tex")
    try:
        balanced.write_text(repaired, encoding="utf-8")
        _render_latex_pandoc(balanced, out_path, resource_dir=resource_dir)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    finally:
        balanced.unlink(missing_ok=True)
    if resource_dir is not None:
        _externalize_local_images(out_path, resource_dir)
    _inject_mathjax_macros(out_path, macros)
    logger.info(
        "pandoc succeeded on %s after removing %d stray unclosed brace(s)",
        source, len(positions),
    )
    return True


def _inject_mathjax_macros(
    html_path: Path, macros: dict[str, MacroValue] | None,
) -> None:
    """Splice a ``window.MathJax`` macro config into the rendered HTML, just
    before pandoc's MathJax loader ``<script>``.

    Always injects the curated package macros (so ``\\mathbbm`` etc. render even
    for papers with no custom preamble); ``macros`` adds the paper's own author
    macros on top. No-op when the HTML has no MathJax loader (e.g. a render with
    no math, or a non-pandoc fallback path)."""
    html = html_path.read_text(encoding="utf-8")
    m = _MATHJAX_SCRIPT_RE.search(html)
    if m is None:
        return
    config = build_mathjax_config_script(macros)
    new_html = html[: m.start()] + config + "\n  " + html[m.start() :]
    html_path.write_text(new_html, encoding="utf-8")


def _externalize_local_images(html_path: Path, resource_dir: Path) -> None:
    """Rewrite ``<img src="rel/path">`` referencing local raster files under
    ``resource_dir`` into a relative ``asset/<path>`` URL, where ``<path>`` is
    the figure's location relative to the HTML file's own directory.

    The Citation Canvas loads ``source.html`` via a backend URL
    (``/papers/content/{id}/html``), so a relative ``asset/`` src resolves to
    ``/papers/content/{id}/asset/<path>`` — served lazily as a file by
    ``serve_asset``. We deliberately do NOT base64-inline figures: a paper with
    70MB of figures produced a 70MB HTML that OOM'd the iframe (arxiv:2605.02881).

    Remote (http/https), data:, and already-rewritten (asset/) srcs are left
    untouched; so are missing or non-raster files."""
    html = html_path.read_text(encoding="utf-8")
    base_dir = html_path.parent.resolve()

    def _sub(m: re.Match[str]) -> str:
        pre, src, post = m.group(1), m.group(2), m.group(3)
        if src.startswith(("data:", "http://", "https://", "//", "asset/")):
            return m.group(0)
        figure = resource_dir / src
        if figure.suffix.lower() not in _IMG_MIME or not figure.is_file():
            return m.group(0)
        try:
            rel = figure.resolve().relative_to(base_dir)
        except ValueError:
            # Figure lives outside the served HTML's directory — can't form a
            # relative asset URL the iframe would resolve. Leave it as-is.
            return m.group(0)
        return f"{pre}asset/{rel.as_posix()}{post}"

    new_html = _IMG_SRC_RE.sub(_sub, html)
    if new_html != html:
        html_path.write_text(new_html, encoding="utf-8")


def _externalize_data_uri_images(html: str, *, out_dir: Path, html_dir: Path) -> str:
    """Extract inline ``data:image/...;base64,...`` ``<img>`` srcs to files under
    ``out_dir`` and rewrite each to a relative ``asset/<path>`` URL (relative to
    ``html_dir``, the served HTML's directory). Used for PyMuPDF's PDF-page HTML,
    which inlines every page image as a data: URI. Undecodable payloads are left
    inline (defensive — never raise mid-render)."""
    out_dir_resolved = out_dir.resolve()
    base_dir = html_dir.resolve()
    counter = 0
    made_dir = False

    def _sub(m: re.Match[str]) -> str:
        nonlocal counter, made_dir
        pre, subtype, b64, post = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        try:
            data = base64.b64decode(b64)
        except ValueError:  # binascii.Error subclasses ValueError
            return m.group(0)
        if not made_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            made_dir = True
        fname = f"img_{counter}{_DATA_URI_EXT.get(subtype, '.png')}"
        counter += 1
        (out_dir / fname).write_bytes(data)
        try:
            rel = (out_dir_resolved / fname).relative_to(base_dir)
        except ValueError:
            return m.group(0)
        return f"{pre}asset/{rel.as_posix()}{post}"

    return _DATA_URI_IMG_RE.sub(_sub, html)


def _render_latex_pylatexenc(tex_path: Path, out_path: Path) -> None:
    text = LatexNodes2Text().latex_to_text(
        tex_path.read_text(encoding="utf-8", errors="ignore"),
    )
    # Minimal HTML envelope so the canvas can scroll-into-view by char offsets.
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = "<pre style='white-space:pre-wrap'>" + escaped + "</pre>"
    out_path.write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{body}</body></html>",
        encoding="utf-8",
    )


def _render_latex_raw_envelope(tex_path: Path, out_path: Path) -> None:
    """Last-resort HTML envelope: HTML-escape the raw .tex bytes inside <pre>.

    Used when both pandoc and pylatexenc fail to parse the source. Citation
    Canvas chunk navigation still works because chunk offsets are computed
    against the flattened LaTeX body, not this HTML view.
    """
    import html

    raw = tex_path.read_bytes().decode("utf-8", errors="replace")
    body = "<pre style='white-space:pre-wrap'>" + html.escape(raw) + "</pre>"
    out_path.write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{body}</body></html>",
        encoding="utf-8",
    )
