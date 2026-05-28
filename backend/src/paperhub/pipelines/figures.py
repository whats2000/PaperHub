"""Normalize LaTeX figure references for HTML rendering (Citation Canvas).

arxiv figures are commonly PDF/EPS and frequently referenced WITHOUT an
extension (``\\includegraphics{figs/fig1}`` -> ``figs/fig1.pdf``). Both break
HTML ``<img>`` embedding: pandoc emits the path verbatim, can't find an
extensionless file to embed, and a browser can't render PDF in an ``<img>``
anyway. This pass rasterizes PDF figures to PNG and rewrites each reference to
a browser-renderable raster with an explicit extension, so pandoc's
``--embed-resources`` inlines them.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

# \includegraphics[opts]{path} — capture the {path} so we can rewrite it.
_INCLUDEGRAPHICS_RE = re.compile(r"(\\includegraphics(?:\[[^\]]*\])?\s*\{)([^}]+)(\})")
# Same command WITH its option bracket, for the strip helper below. Two
# capture groups so the substitution can keep \includegraphics + the file
# token while discarding only the options.
_INCLUDEGRAPHICS_WITH_OPTS_RE = re.compile(
    r"(\\includegraphics)\[[^\]]*\]\s*(\{[^}]+\})"
)
# Tried in order when a reference has no extension (LaTeX graphics resolution).
_RESOLVE_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps")
_RASTERIZE_DPI = 150


def _resolve_figure(ref: str, resource_dir: Path) -> Path | None:
    direct = resource_dir / ref
    if direct.is_file():
        return direct
    if Path(ref).suffix == "":
        for ext in _RESOLVE_EXTS:
            cand = resource_dir / (ref + ext)
            if cand.is_file():
                return cand
    return None


def _rasterize_pdf(pdf_path: Path, png_path: Path) -> None:
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=_RASTERIZE_DPI)
        pix.save(str(png_path))


def strip_includegraphics_options(tex: str) -> str:
    """Drop the ``[options]`` bracket from each ``\\includegraphics`` so the
    rendered HTML ``<img>`` inherits its natural size.

    LaTeX uses ``[width=0.5\\textwidth]`` to fit a figure into a narrow
    print column. Pandoc faithfully translates that to
    ``style="width:50.0%"`` on the ``<img>`` — which on a wide HTML canvas
    shrinks the figure to half-width for no reason, undoing the
    higher-DPI rasterisation we paid for. Stripping the bracket lets CSS
    size the image to fit the container (or smaller, on a narrow viewport)
    while keeping the underlying pixels crisp.

    Other ``\\includegraphics`` options (``scale``, ``angle``, ``clip``,
    ``keepaspectratio``) are stripped too — none of them translate
    meaningfully to a same-origin HTML viewer, and keeping the helper
    one-pass + intention-clear beats a per-option allowlist.
    """
    return _INCLUDEGRAPHICS_WITH_OPTS_RE.sub(r"\1\2", tex)


def rasterize_and_normalize_figures(tex: str, resource_dir: Path) -> str:
    """Return ``tex`` with figure references normalized for HTML embedding.

    Side effect: writes a ``.png`` next to each PDF figure it rasterizes.
    Resolution failures and rasterize errors are logged and left as-is (the
    renderer's downstream fallbacks still produce a usable artefact).
    """

    def _sub(m: re.Match[str]) -> str:
        pre, ref, post = m.group(1), m.group(2).strip(), m.group(3)
        resolved = _resolve_figure(ref, resource_dir)
        if resolved is None:
            return m.group(0)
        target = resolved
        if resolved.suffix.lower() in (".pdf", ".eps"):
            png = resolved.with_suffix(".png")
            try:
                if not png.is_file():
                    _rasterize_pdf(resolved, png)
                target = png
            except Exception as exc:  # noqa: BLE001 — pymupdf raises bare Exceptions
                logger.warning("figure rasterize failed for %s: %s", resolved, exc)
                return m.group(0)
        new_ref = target.relative_to(resource_dir).as_posix()
        if new_ref == ref:
            return m.group(0)
        return f"{pre}{new_ref}{post}"

    return _INCLUDEGRAPHICS_RE.sub(_sub, tex)
