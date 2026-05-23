"""Extract figures, equations, and sections from a flattened LaTeX document.

Given the flattened body text produced by :func:`paperhub.pipelines.extract.extract_latex`
and the source directory where figure files live, this module builds a
:class:`~paperhub.pipelines.paper_asset.PaperAsset` with:

* :class:`~paperhub.pipelines.paper_asset.SectionAsset` — one per ``\\section{…}`` in
  document order.
* :class:`~paperhub.pipelines.paper_asset.FigureAsset` — one per
  ``\\begin{figure}…\\end{figure}`` environment that resolves to a real file.
  Raster copies (.png/.jpg/.jpeg) are copied; .pdf/.eps are rasterized to PNG
  via pymupdf (same approach as
  :func:`paperhub.pipelines.figures.rasterize_and_normalize_figures` — provenance).
* :class:`~paperhub.pipelines.paper_asset.EquationAsset` — one per
  ``\\begin{equation}…``, ``\\begin{align}…``, or ``\\[…\\]`` environment.

The staged figure images are written to ``<source_dir>/asset/figures/`` so
:func:`paperhub.pipelines.paper_asset.paper_asset_dir` resolves them.
:func:`paperhub.pipelines.paper_asset.write_paper_asset` is called separately
by the pipeline; this function returns the PaperAsset and only stages files.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import pymupdf

from paperhub.pipelines.paper_asset import (
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")

# figure / figure* environments (DOTALL so newlines are spanned)
_FIGURE_ENV_RE = re.compile(
    r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", re.DOTALL
)

# \includegraphics[opts]{path} — opts bracket is optional
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\s*\{([^}]+)\}")

# \caption{ … } — handle one level of nested braces
_CAPTION_RE = re.compile(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}")

# equation / align environments (DOTALL)
_EQUATION_ENV_RE = re.compile(
    r"\\begin\{(equation\*?|align\*?)\}(.*?)\\end\{\1\}", re.DOTALL
)

# \[ … \] display math (DOTALL)
_DISPLAY_MATH_RE = re.compile(r"\\\[(.*?)\\\]", re.DOTALL)

# LaTeX commands to strip from captions: \cmd{inner} → inner  or  \cmd → ""
_CMD_INNER_RE = re.compile(r"\\[A-Za-z]+\{([^{}]*)\}")
_CMD_BARE_RE = re.compile(r"\\[A-Za-z]+")
_TILDE_RE = re.compile(r"~")
_DOUBLE_BACKSLASH_RE = re.compile(r"\\\\")
_WHITESPACE_RE = re.compile(r"\s+")

# Extensions tried in order when reference has no extension (mirrors figures.py)
_RESOLVE_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps")
_RASTERIZE_DPI = 200   # higher than figures.py's 150 — asset quality matters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section_map(flattened_text: str) -> list[tuple[int, str]]:
    """Return ``[(char_offset, name), …]`` sorted by offset for all ``\\section``."""
    return [(m.start(), m.group(1).strip()) for m in _SECTION_RE.finditer(flattened_text)]


def _current_section(offset: int, sections: list[tuple[int, str]]) -> str | None:
    """Return the name of the most recent section before *offset*."""
    name: str | None = None
    for sec_off, sec_name in sections:
        if sec_off <= offset:
            name = sec_name
        else:
            break
    return name


def _resolve_figure_path(ref: str, latex_source_dir: Path) -> Path | None:
    """Resolve a ``\\includegraphics`` reference to a real file.

    Strategy (mirrors :func:`paperhub.pipelines.figures._resolve_figure`):
    1. Try the reference as-is.
    2. If no extension, try each of _RESOLVE_EXTS appended.
    3. Try ``ref + ext`` with a recursive glob on basename (for sub-dirs).
    """
    # Strip leading/trailing whitespace that sometimes sneaks in
    ref = ref.strip()

    direct = latex_source_dir / ref
    if direct.is_file():
        return direct

    if not Path(ref).suffix:
        for ext in _RESOLVE_EXTS:
            cand = latex_source_dir / (ref + ext)
            if cand.is_file():
                return cand
        # Last resort: search by basename recursively (handles re-rooted tarballs)
        basename = Path(ref).name
        for ext in _RESOLVE_EXTS:
            hits = list(latex_source_dir.rglob(basename + ext))
            if hits:
                return hits[0]
    return None


def _rasterize_to_png(src: Path, dst: Path) -> None:
    """Rasterize the first page of *src* (.pdf/.eps) to *dst* (PNG).

    Adapted from :func:`paperhub.pipelines.figures._rasterize_pdf`; using a
    higher DPI (200 vs 150) for asset quality.
    """
    with pymupdf.open(src) as doc:  # type: ignore[no-untyped-call]
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=_RASTERIZE_DPI)
        pix.save(str(dst))


def _clean_caption(raw: str, max_len: int = 300) -> str:
    """Convert a LaTeX caption body to readable plain text.

    Steps:
    1. Replace ``\\cmd{inner}`` with ``inner`` (strip formatting commands,
       keep text content — e.g. ``\\textbf{Loss}`` → ``Loss``).
    2. Remove bare ``\\cmd`` tokens.
    3. Replace ``\\\\`` with a space.
    4. Replace ``~`` with a space.
    5. Collapse whitespace.
    6. Truncate to *max_len*.
    """
    text = raw
    # Iteratively expand \cmd{inner} → inner (handles chains like \textbf{\emph{x}})
    prev = None
    while prev != text:
        prev = text
        text = _CMD_INNER_RE.sub(r"\1", text)
    text = _CMD_BARE_RE.sub(" ", text)
    text = _DOUBLE_BACKSLASH_RE.sub(" ", text)
    text = _TILDE_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:max_len]


def _stage_figure(
    resolved: Path,
    figures_dir: Path,
    fig_index: int,
) -> str:
    """Copy or rasterize *resolved* into *figures_dir*, return relative image_path.

    Returns a path like ``"figures/fig-001.png"`` relative to the asset dir.
    Raises on copy/rasterize failure (caller wraps in try/except).
    """
    figures_dir.mkdir(parents=True, exist_ok=True)
    suffix = resolved.suffix.lower()
    if suffix in (".pdf", ".eps"):
        dst_name = f"fig-{fig_index:03d}.png"
        dst = figures_dir / dst_name
        _rasterize_to_png(resolved, dst)
    else:
        dst_name = f"fig-{fig_index:03d}{resolved.suffix}"
        dst = figures_dir / dst_name
        shutil.copy2(resolved, dst)
    return f"figures/{dst_name}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def latex_source_to_asset(
    latex_source_dir: Path,
    flattened_text: str,
    *,
    source_dir: Path,
) -> PaperAsset:
    """Extract figures, equations, and sections from *flattened_text*.

    Parameters
    ----------
    latex_source_dir:
        The ``source/`` directory where figure files live (resolved by
        ``\\includegraphics`` references).
    flattened_text:
        The flattened LaTeX body (preamble already stripped; ``\\input``/
        ``\\include`` already inlined) from
        :func:`paperhub.pipelines.extract.extract_latex`.
    source_dir:
        The paper's cache root.  Staged figure images are written to
        ``<source_dir>/asset/figures/``; JSON manifests are written separately
        by :func:`paperhub.pipelines.paper_asset.write_paper_asset`.

    Returns
    -------
    PaperAsset
        Populated asset bundle (figures/equations/sections).  Figure image
        files are already on disk under the asset dir.
    """
    asset_dir = paper_asset_dir(source_dir)
    figures_dir = asset_dir / "figures"

    # --- 1. Sections ---
    sec_map = _section_map(flattened_text)
    sections: list[SectionAsset] = [
        SectionAsset(name=name, order=i)
        for i, (_, name) in enumerate(sec_map)
    ]

    # --- 2. Figures ---
    figures: list[FigureAsset] = []
    fig_index = 1
    for m in _FIGURE_ENV_RE.finditer(flattened_text):
        env_body = m.group(1)
        env_start = m.start()

        # Find \includegraphics ref
        ig_m = _INCLUDEGRAPHICS_RE.search(env_body)
        if ig_m is None:
            continue
        ref = ig_m.group(1).strip()

        # Find \caption
        cap_m = _CAPTION_RE.search(env_body)
        raw_caption = cap_m.group(1) if cap_m else ""
        caption = _clean_caption(raw_caption)

        # Resolve file
        resolved = _resolve_figure_path(ref, latex_source_dir)
        if resolved is None:
            logger.debug("latex_to_asset: skipping unresolvable figure %r", ref)
            continue

        # Stage the file
        try:
            image_path = _stage_figure(resolved, figures_dir, fig_index)
        except Exception:  # noqa: BLE001 — pymupdf / IO errors
            logger.warning(
                "latex_to_asset: failed to stage figure %s (index %d), skipping",
                resolved,
                fig_index,
                exc_info=True,
            )
            continue

        section_name = _current_section(env_start, sec_map)
        figures.append(
            FigureAsset(
                id=f"fig-{fig_index:03d}",
                caption=caption,
                page=None,
                section=section_name,
                image_path=image_path,
            )
        )
        fig_index += 1

    # --- 3. Equations ---
    equations: list[EquationAsset] = []
    eq_index = 1

    for m in _EQUATION_ENV_RE.finditer(flattened_text):
        body = m.group(2).strip()
        section_name = _current_section(m.start(), sec_map)
        equations.append(
            EquationAsset(
                id=f"eq-{eq_index:03d}",
                latex=body,
                section=section_name,
            )
        )
        eq_index += 1

    for m in _DISPLAY_MATH_RE.finditer(flattened_text):
        body = m.group(1).strip()
        section_name = _current_section(m.start(), sec_map)
        equations.append(
            EquationAsset(
                id=f"eq-{eq_index:03d}",
                latex=body,
                section=section_name,
            )
        )
        eq_index += 1

    return PaperAsset(figures=figures, equations=equations, sections=sections)
