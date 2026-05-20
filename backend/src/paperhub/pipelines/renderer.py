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

logger = logging.getLogger(__name__)

_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)
_IMG_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

# pandoc can HANG (not just exit non-zero) on pathological LaTeX. Without a
# bound the hanging subprocess parks the whole ingest until the worker OOMs
# (arxiv:2410.12557 reproduced this). Cap it and treat a timeout like any
# other pandoc failure — fall back to pylatexenc, then the raw envelope.
_PANDOC_TIMEOUT_SECONDS = 60


def render_html(
    *,
    source: Path,
    kind: Literal["latex", "pdf"],
    out_path: Path,
    resource_dir: Path | None = None,
) -> Path:
    """Render ``source`` to an HTML artefact at ``out_path``.

    ``resource_dir`` (latex only) is where figures referenced by the flattened
    source actually live — typically the extracted ``source/`` subtree, a
    different directory from the flattened ``.tex``. pandoc searches it via
    ``--resource-path``; figures are then inlined as data: URIs by
    ``_inline_local_images`` so the artefact is self-contained for the Citation
    Canvas (Plan D). Math is rendered via an EXTERNAL MathJax CDN ``<script>``
    (``--mathjax``, not ``--embed-resources``) so multi-line environments like
    ``\\begin{aligned}`` render in the browser without fetching+inlining ~1.3MB
    of MathJax into every paper at ingest time.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "pdf":
        _render_pdf(source, out_path)
    elif kind == "latex":
        if shutil.which("pandoc"):
            try:
                _render_latex_pandoc(source, out_path, resource_dir=resource_dir)
                if resource_dir is not None:
                    _inline_local_images(out_path, resource_dir)
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
    out_path.write_text("".join(pieces), encoding="utf-8")


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
        # into every paper at ingest (~12s/paper + network dependency). Figures
        # are inlined separately by _inline_local_images.
        "--mathjax",
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


def _inline_local_images(html_path: Path, resource_dir: Path) -> None:
    """Rewrite ``<img src="rel/path">`` referencing local raster files under
    ``resource_dir`` into base64 ``data:`` URIs, so the HTML is self-contained
    without pandoc's --embed-resources (which would also fetch+inline MathJax).
    Remote (http/https) and already-inlined (data:) srcs are left untouched;
    missing or non-raster files are left as-is."""
    html = html_path.read_text(encoding="utf-8")

    def _sub(m: re.Match[str]) -> str:
        pre, src, post = m.group(1), m.group(2), m.group(3)
        if src.startswith(("data:", "http://", "https://", "//")):
            return m.group(0)
        figure = resource_dir / src
        mime = _IMG_MIME.get(figure.suffix.lower())
        if mime is None or not figure.is_file():
            return m.group(0)
        try:
            b64 = base64.b64encode(figure.read_bytes()).decode("ascii")
        except OSError as exc:
            logger.warning("image inline failed for %s: %s", figure, exc)
            return m.group(0)
        return f"{pre}data:{mime};base64,{b64}{post}"

    new_html = _IMG_SRC_RE.sub(_sub, html)
    if new_html != html:
        html_path.write_text(new_html, encoding="utf-8")


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
