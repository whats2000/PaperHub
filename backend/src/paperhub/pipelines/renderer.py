"""Render paper source to HTML for the Citation Canvas (FR-03).

Strategy:
- LaTeX: pandoc primary (good math + figure support). pylatexenc fallback
  when pandoc is absent OR exits non-zero (idiosyncratic LaTeX is common).
- PDF: PyMuPDF's HTML export (preserves layout enough for highlight scrolling).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import pymupdf
from pylatexenc.latex2text import LatexNodes2Text

logger = logging.getLogger(__name__)

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
    """Render ``source`` to a self-contained HTML artefact at ``out_path``.

    ``resource_dir`` (latex only) is where figures referenced by the flattened
    source actually live — typically the extracted ``source/`` subtree, which
    is a different directory from the flattened ``.tex``. pandoc searches it via
    ``--resource-path`` and inlines the figures (``--embed-resources``) so the
    Citation Canvas (Plan D) renders images regardless of where the HTML is
    served from.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "pdf":
        _render_pdf(source, out_path)
    elif kind == "latex":
        if shutil.which("pandoc"):
            try:
                _render_latex_pandoc(source, out_path, resource_dir=resource_dir)
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
        # Inline figures/CSS as data: URIs so the artefact is self-contained
        # (the Citation Canvas serves it independent of the source tree).
        "--embed-resources",
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
