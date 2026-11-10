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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from paperhub.pipelines.figure_composite import Panel, compose_panels, width_share
from paperhub.pipelines.figure_numbers import _drop_subfigure_groups, _strip_comments
from paperhub.pipelines.figure_wrappers import unwrap_figure_wrappers
from paperhub.pipelines.paper_asset import (
    EquationAsset,
    FigureAsset,
    PaperAsset,
    SectionAsset,
    paper_asset_dir,
    read_paper_asset,
)
from paperhub.pipelines.sentinels import inject_figure_sentinels

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}")

# figure / figure* environments (DOTALL so newlines are spanned)
_FIGURE_ENV_RE = re.compile(
    r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", re.DOTALL
)

_COMMENT_ENV_RE = re.compile(r"\\begin\{comment\}.*?\\end\{comment\}", re.DOTALL)

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


def in_latex_comment(text: str, pos: int) -> bool:
    """Whether ``pos`` lies after an unescaped ``%`` on its line."""
    i = text.rfind("\n", 0, pos) + 1
    while i < pos:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "%":
            return True
        i += 1
    return False


def figure_env_starts(flattened_text: str) -> list[int]:
    """Start offset of every figure environment, by ``env_index`` (the
    ordinal ``latex_source_to_asset`` records on each figure)."""
    return [m.start() for m in _FIGURE_ENV_RE.finditer(flattened_text)]


@dataclass(frozen=True)
class FigureEnv:
    """A figure environment with a resolvable image, in document order."""

    env_index: int          # ordinal among all figure environments
    env_start: int
    caption: str
    resolved: Path
    commented: bool         # inside a ``%`` comment: not part of the paper
    panels: list[PanelSource] = field(default_factory=list)
    # The caption the paper prints for the figure: its own top-level
    # \caption (not a panel's, not a commented draft), long form, braces
    # parsed fully, citation keys and labels dropped. ``caption`` is the
    # earlier extraction, kept to match assets stored before this.
    main_caption: str = ""


# Commands whose argument is not printed as text (a number or nothing).
_REF_CMD_RE = re.compile(
    r"\\(?:[a-zA-Z]*cite[a-zA-Z]*|ref|eqref|[cC]ref|autoref|label|footnote)\*?"
    r"(?:\[[^\]]*\])*\{[^{}]*\}")


def _braced(text: str, i: int, opener: str, closer: str) -> tuple[str, int] | None:
    """The balanced ``opener…closer`` group at ``text[i]`` (after blanks):
    its inner text and the index after it; None when there is none."""
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if i >= len(text) or text[i] != opener:
        return None
    depth, j = 0, i
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        depth += text[j] == opener
        depth -= text[j] == closer
        j += 1
        if depth == 0:
            return text[i + 1:j - 1], j
    return None


def main_caption(env_body: str) -> str:
    """The figure's own printed caption (see ``FigureEnv.main_caption``): the
    first top-level ``\\caption`` — an environment holding two side-by-side
    figures has two, and its stored image is the first figure's."""
    body = _drop_subfigure_groups(_strip_comments(env_body))
    for m in re.finditer(r"\\caption(?![a-zA-Z])\*?", body):
        i = m.end()
        short = _braced(body, i, "[", "]")
        if short is not None:
            i = short[1]
        long = _braced(body, i, "{", "}")
        if long is not None:
            return _clean_caption(_REF_CMD_RE.sub("", long[0]).replace("\\ ", " "))
    return ""


def figure_envs(latex_source_dir: Path, flattened_text: str) -> list[FigureEnv]:
    """The figure environments ``latex_source_to_asset`` numbers: those with
    an ``\\includegraphics`` that resolves to a file."""
    out: list[FigureEnv] = []
    # \begin{comment}...\end{comment} (the comment package) hides its body.
    comment_spans = [
        (c.start(), c.end()) for c in _COMMENT_ENV_RE.finditer(flattened_text)
        if not in_latex_comment(flattened_text, c.start())]
    for env_index, m in enumerate(_FIGURE_ENV_RE.finditer(flattened_text)):
        env_body = m.group(1)
        ig_m = _INCLUDEGRAPHICS_RE.search(env_body)
        if ig_m is None:
            continue
        ref = ig_m.group(1).strip()
        cap_m = _CAPTION_RE.search(env_body)
        caption = _clean_caption(cap_m.group(1) if cap_m else "")
        resolved = _resolve_figure_path(ref, latex_source_dir)
        if resolved is None:
            logger.debug("latex_to_asset: skipping unresolvable figure %r", ref)
            continue
        commented = in_latex_comment(flattened_text, m.start()) or any(
            s <= m.start() < e for s, e in comment_spans)
        out.append(FigureEnv(
            env_index=env_index, env_start=m.start(), caption=caption,
            resolved=resolved, commented=commented,
            panels=figure_panels(env_body, latex_source_dir),
            main_caption=main_caption(env_body),
        ))
    return out


_INCLUDEGRAPHICS_OPTS_RE = re.compile(
    r"\\includegraphics\s*(\[[^\]]*\])?\s*\{([^}]+)\}")
_PANEL_LABEL_RE = re.compile(
    r"^\s*(?:(?:\\label\{[^}]*\}|%[^\n]*\n)\s*)*\(([a-z])\)\s*([^\\{}%]*)")
_ROW_BREAK_RE = re.compile(r"\\\\|\\par\b|\\newline\b|\n\s*\n")


@dataclass(frozen=True)
class PanelSource:
    resolved: Path
    width: float | None
    new_row: bool
    label: str | None


def figure_panels(env_body: str, latex_source_dir: Path) -> list[PanelSource]:
    """Every resolvable image of a figure environment, in order, with its
    line share, whether a line break comes before it, and its ``(a) caption``
    (sub-figure wrappers unwrapped first, which writes those labels)."""
    body = unwrap_figure_wrappers(f"\\begin{{figure}}{env_body}\\end{{figure}}")
    matches = list(_INCLUDEGRAPHICS_OPTS_RE.finditer(body))
    panels: list[PanelSource] = []
    for i, m in enumerate(matches):
        resolved = _resolve_figure_path(m.group(2), latex_source_dir)
        if resolved is None:
            continue
        before = body[matches[i - 1].end():m.start()] if i else ""
        after = body[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(body)]
        lab = _PANEL_LABEL_RE.match(after)
        label = (f"({lab.group(1)}) {' '.join(lab.group(2).split())}".strip()
                 if lab else None)
        panels.append(PanelSource(
            resolved=resolved, width=width_share(m.group(1) or ""),
            new_row=bool(i and _ROW_BREAK_RE.search(before)), label=label,
        ))
    return panels


def anchor_asset_figures(marked: str, cache_dir: Path) -> tuple[str, set[str]]:
    """Put each asset figure's ``phfig`` sentinel before its environment in
    the (chunk-sentinel-marked) render source; returns the anchored ids."""
    asset = read_paper_asset(cache_dir)
    figures = [(f.id, f.env_index) for f in (asset.figures if asset else [])
               if f.env_index is not None]
    return inject_figure_sentinels(marked, figure_env_starts(marked), figures)


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


def stage_panels(panels: list[PanelSource], figures_dir: Path, fig_index: int) -> str:
    """Compose a multi-panel figure into ``figures/fig-NNN.png`` (PDF / EPS
    panels rasterised first); returns its asset-relative path."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staged: list[Panel] = []
        for n, p in enumerate(panels):
            if p.resolved.suffix.lower() in (".pdf", ".eps"):
                raster = Path(tmp) / f"panel-{n}.png"
                _rasterize_to_png(p.resolved, raster)
            else:
                raster = p.resolved
            staged.append(Panel(raster, p.width, p.new_row, p.label))
        dst = figures_dir / f"fig-{fig_index:03d}.png"
        compose_panels(staged, dst)
    return f"figures/{dst.name}"


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
    for env in figure_envs(latex_source_dir, flattened_text):
        env_index, env_start, caption, resolved = (
            env.env_index, env.env_start, env.main_caption or env.caption, env.resolved)
        if env.commented:
            # Commented out: not part of the paper. It keeps its number, as
            # before this check, so later figures keep their ids (slide decks
            # refer to figures by id).
            fig_index += 1
            continue

        # Stage the file (several panels: one composite image of them all)
        try:
            image_path = (stage_panels(env.panels, figures_dir, fig_index)
                          if len(env.panels) > 1
                          else _stage_figure(resolved, figures_dir, fig_index))
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
                env_index=env_index,
                panels=len(env.panels) if len(env.panels) > 1 else None,
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
