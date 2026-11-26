"""Pre-rasterise TikZ-drawn figures so pandoc can embed them as ``<img>``.

Pandoc has no executor for TikZ / forest / pgfplots / circuitikz. A figure
drawn programmatically with one of those environments — e.g. the survey
roadmap taxonomy tree in arXiv:2503.07137 — gets dumped to HTML as the
literal source (``[Mixture of Experts (MoE) [Basics of MoE [Gating
Function …]]]``), which is unreadable and breaks the Citation Canvas.

This module finds each TikZ environment in the flattened LaTeX source,
compiles it as a ``standalone`` document via ``pdflatex`` (using the
paper's own preamble + any colour / style macros that appear in the body
*before* the figure — surveys define ``\\definecolor`` and
``\\tikzstyle`` inline), rasterises the resulting PDF to PNG, and
rewrites the environment to ``\\includegraphics{<png>}``. Pandoc then
embeds the figure normally.

``pdflatex`` is already a hard runtime dependency of the slide pipeline
(SRS), so this adds no new requirement. Failures are graceful — if a
block won't compile (missing package, syntax error, pdflatex absent) the
original TikZ source is left in place and the rest of the document still
renders cleanly.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

# Conference/page-layout styles (neurips_2025, naaclhlt2019, geometry, …)
# impose the paper's page geometry on the standalone figure — KVzip's
# figure spilled to page 2 of a letter page, BERT's drew outside the page
# box. Tables hit the same thing first; share their list.
from paperhub.pipelines.table_figures import _LAYOUT_PACKAGE_RE

logger = logging.getLogger(__name__)

# TikZ-bearing environments worth rasterising. Each is matched non-greedy
# with DOTALL so newlines span; ``(\1)`` ensures begin/end pair up. For
# the rare case of nested envs the outer match closes at the FIRST inner
# ``\end{X}`` — which mis-detects a nested ``forest`` inside ``forest``.
# In practice surveys + papers don't nest like that; if it ever becomes an
# issue the fix is a brace-aware parser, not a smarter regex.
_TIKZ_ENV_RE = re.compile(
    r"\\begin\{(tikzpicture|forest|circuitikz|pgfpicture)\}.*?\\end\{\1\}",
    re.DOTALL,
)

# Macros the figure may depend on, mined from the body BEFORE the env so
# colours + styles defined inline still apply in the standalone compile.
# Each pattern is intentionally simple — false positives are harmless
# (extra defs in a standalone preamble compile fine) but a miss could
# break the figure.
_DEFINECOLOR_RE = re.compile(
    r"\\definecolor\{[^}]+\}\{[^}]+\}\{[^}]+\}"
)
# \tikzstyle{name}=[opts] — opts may span lines and hold ``]`` inside braces
# (``[{Bar[width=3pt]}-{Bar[width=3pt]}]``), so only the head is a regex and
# ``_tikzstyle_defs`` finds the closing bracket.
_TIKZSTYLE_HEAD_RE = re.compile(r"\\tikzstyle\{[^}]+\}\s*=\s*\[")
# \tikzset{name/.style=...} — group can be multi-line; allow one level of
# nested braces.
_TIKZSET_RE = re.compile(
    r"\\tikzset\{(?:[^{}]|\{[^{}]*\})*\}", re.DOTALL
)
# \setlength{\len}{value} / \setlength\len{value} — tikzplotlib figures size
# their axis by a length the body resets right before each figure.
_SETLENGTH_RE = re.compile(
    r"\\setlength\s*(?:\{\\[A-Za-z@]+\}|\\[A-Za-z@]+)\s*\{[^{}]*\}"
)
# Strip \documentclass from the paper's preamble before reusing it under
# our own standalone classdec — IEEEtran, article, etc. clash with
# standalone.
_DOCUMENTCLASS_RE = re.compile(
    r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}\s*"
)

# Bedrock packages every TikZ figure tends to want. The paper's own
# preamble layers on top, so explicit \\usepackage / \\usetikzlibrary
# declarations win when the paper has them. This default catches papers
# that lean on a TikZ command without the matching library import.
_STANDALONE_PREAMBLE = r"""\documentclass[border=10pt]{standalone}
\usepackage{tikz}
\usepackage{forest}
\usepackage{pgfplots}
\usepackage{amsmath,amssymb,amsfonts}
\usepackage{xcolor}
\usepackage{bm}
\usetikzlibrary{trees,positioning,arrows,arrows.meta,calc,shapes,decorations.pathreplacing,fit,backgrounds,patterns,shadows,fadings,intersections}
"""

_PDFLATEX_TIMEOUT_SECONDS = 60

# A ``\foreach`` loop written OUTSIDE a TikZ env, repeating one per item
# (arXiv:2505.23416 stacks five heatmaps this way). The env alone can't
# compile (it uses the loop variables) and pandoc can't parse the loop, so
# the whole ``\foreach … in {list} {body}`` is the figure. The spec part
# (``\pdf/\title [count=\i]``) never holds a brace.
_FOREACH_HEAD_RE = re.compile(r"\\foreach\b[^{}]*?\bin\s*(?=\{)")

# varwidth lets a loop body with \par / \vspace stack vertically inside the
# standalone class; it shrinks to the natural width, so the cap is generous.
_VARWIDTH_OPTION = "varwidth=60cm"

# ``[remember picture, overlay]`` draws on the page itself (a dashed rule
# beside a table, a cover background) — decoration, not a figure.
_OVERLAY_HEAD_RE = re.compile(
    r"\\begin\{tikzpicture\}\s*\[[^\]]*\boverlay\b"
)

# ``\addplot … raw gnuplot, id=<id>`` reads ``<jobname>.<id>.table``; arXiv
# sources ship those precomputed under the paper's own jobname.
_GNUPLOT_ID_RE = re.compile(r"\bid\s*=\s*([\w.+-]+)")

# A rendered figure must hold some ink darker than this (0-255 grey).
_INK_THRESHOLD = 200


def _is_commented(tex: str, pos: int) -> bool:
    """True when ``pos`` sits after an unescaped ``%`` on its line."""
    i = tex.rfind("\n", 0, pos) + 1
    while i < pos:
        if tex[i] == "\\":
            i += 2
            continue
        if tex[i] == "%":
            return True
        i += 1
    return False


def _tikz_env_spans(tex: str) -> list[tuple[int, int]]:
    """Spans of the TikZ envs that are part of the document.

    An env whose ``\\begin`` is commented out (a figure the author
    disabled) is skipped, and the search resumes right after that
    ``\\begin``, so a commented begin without a commented end can't
    swallow the real figure that follows.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    while (m := _TIKZ_ENV_RE.search(tex, pos)) is not None:
        if _is_commented(tex, m.start()):
            pos = m.start() + 1
            continue
        if not _OVERLAY_HEAD_RE.match(m.group(0)):
            spans.append((m.start(), m.end()))
        pos = m.end()
    return spans


def _gnuplot_jobname(env: str, source_dir: Path) -> str | None:
    """The jobname whose precomputed ``<job>.<id>.table`` files this env's
    ``raw gnuplot`` plots read, when the paper ships them."""
    if "gnuplot" not in env:
        return None
    for plot_id in _GNUPLOT_ID_RE.findall(env):
        suffix = f".{plot_id}.table"
        for table in source_dir.glob(f"*{suffix}"):
            if table.name.endswith(suffix) and len(table.name) > len(suffix):
                return table.name[: -len(suffix)]
    return None


def _is_paper_sized(width: float, height: float) -> bool:
    """True for a US-letter or A4 page (either orientation, ±2pt)."""
    for w, h in ((612.0, 792.0), (595.28, 841.89)):
        for a, b in ((w, h), (h, w)):
            if abs(width - a) <= 2 and abs(height - b) <= 2:
                return True
    return False


def _has_ink(pix: pymupdf.Pixmap) -> bool:
    """True when any pixel is darker than ``_INK_THRESHOLD`` (all pixels
    checked, so a figure of thin lines still counts)."""
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pix)  # type: ignore[no-untyped-call]
    return bool(gray.samples) and min(gray.samples) < _INK_THRESHOLD


def _skip_group(tex: str, i: int) -> int | None:
    """Return the index just past the brace group opening at ``tex[i]``.

    Escaped braces (``\\{``) and ``%`` comments are skipped. ``None`` when
    ``tex[i]`` is not ``{`` or the group never closes.
    """
    if i >= len(tex) or tex[i] != "{":
        return None
    depth = 0
    n = len(tex)
    while i < n:
        c = tex[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            nl = tex.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _foreach_spans(
    tex: str, env_spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Spans of top-level ``\\foreach`` loops whose body holds a TikZ env.

    A loop inside a TikZ env is ordinary TikZ and stays part of that env.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    while (m := _FOREACH_HEAD_RE.search(tex, pos)) is not None:
        start = m.start()
        pos = m.end()
        if _is_commented(tex, start) or any(
            s <= start < e for s, e in env_spans
        ):
            continue
        list_end = _skip_group(tex, m.end())
        if list_end is None:
            continue
        body_start = list_end
        while body_start < len(tex) and tex[body_start].isspace():
            body_start += 1
        body_end = _skip_group(tex, body_start)
        if body_end is None:
            continue
        if not _TIKZ_ENV_RE.search(tex, body_start, body_end):
            continue
        spans.append((start, body_end))
        pos = body_end
    return spans


def _tikzstyle_defs(text: str) -> list[tuple[int, str]]:
    """``(offset, definition)`` for each ``\\tikzstyle{name}=[opts]``.

    Like a LaTeX optional argument, ``opts`` ends at the first ``]`` outside
    braces. An unclosed definition is skipped: a cut-off one would leave an
    open brace that breaks every later figure's compile.
    """
    defs: list[tuple[int, str]] = []
    for m in _TIKZSTYLE_HEAD_RE.finditer(text):
        depth = 0
        i = m.end()
        while i < len(text):
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "]" and depth == 0:
                defs.append((m.start(), text[m.start():i + 1]))
                break
            i += 1
    return defs


def _gather_tikz_context(preamble: str, body_prefix: str) -> str:
    """Build the standalone-doc preamble for one figure.

    Layers:
      - Paper's own preamble (minus ``\\documentclass``, which our
        standalone class replaces). Brings ``\\usepackage{tikz}``,
        ``\\usepackage{forest}``, ``\\usetikzlibrary{…}``, ``\\definecolor``,
        ``\\tikzstyle``, ``\\tikzset``, ``\\newcommand``, etc. — anything
        the figure could legitimately depend on.
      - ``\\definecolor`` / ``\\tikzstyle`` / ``\\tikzset`` / ``\\setlength``
        declarations that appear in the body BEFORE the figure, in document
        order (surveys often define colours inline at the top of the
        document body, not the preamble — arXiv:2503.07137's
        ``\\definecolor{line-color}{RGB}{0,119,255}`` sits in the body at
        flat line 20; tikzplotlib figures reset ``\\figurewidth`` right
        before each figure).
    """
    parts: list[str] = []
    # Paper preamble, minus \\documentclass and page-layout styles.
    parts.append(
        _LAYOUT_PACKAGE_RE.sub("", _DOCUMENTCLASS_RE.sub("", preamble)),
    )
    # Body-prefix macros, in document order.
    body_macros: list[tuple[int, str]] = []
    for pat in (_DEFINECOLOR_RE, _TIKZSET_RE, _SETLENGTH_RE):
        for m in pat.finditer(body_prefix):
            body_macros.append((m.start(), m.group(0)))
    body_macros.extend(_tikzstyle_defs(body_prefix))
    body_macros.sort()
    parts.extend(s for _, s in body_macros)
    return "\n".join(parts)


def _compile_tikz_to_png(
    env: str,
    *,
    preamble: str,
    body_prefix: str,
    png_path: Path,
    dpi: int,
    varwidth: bool = False,
    jobname: str | None = None,
) -> bool:
    """Compile one TikZ env to ``png_path``. Return True on success.

    pdflatex runs in an isolated temp dir so .aux/.log/.pdf droppings
    don't pollute the paper's source/ tree; TEXINPUTS points at the
    paper's source dir (= ``png_path.parent``) so bundled .sty files and
    relative figure paths still resolve. ``varwidth`` lets a ``\\foreach``
    unit's ``\\par`` stack vertically; ``jobname`` (default ``fig``)
    names the compiled file so ``\\jobname``-relative inputs resolve. The
    PNG is written via pymupdf at ``dpi``. Any failure (timeout, no PDF, a
    blank or multi-page render, rasterise error) is logged and returned as
    False, and no PNG is written.
    """
    context = _gather_tikz_context(preamble, body_prefix)
    head = _STANDALONE_PREAMBLE
    if varwidth:
        head = head.replace(
            "[border=10pt]", f"[border=10pt,{_VARWIDTH_OPTION}]", 1,
        )
    # Paper-bundled style files (neurips_2025.sty, …) and figure paths
    # (image/x.pdf) live in the source dir; the trailing path separator
    # keeps the default texmf search path. "." comes first: kpathsea
    # searches TEXINPUTS before the compile dir, so a paper file named like
    # ours (arXiv:1508.07909's subword.tex) would be compiled instead.
    proc_env = dict(os.environ)
    proc_env["TEXINPUTS"] = (
        "." + os.pathsep + str(png_path.parent.resolve()) + os.pathsep
        + proc_env.get("TEXINPUTS", "")
    )
    # \thispagestyle{empty}: a paper's \pagestyle{fancy} would otherwise
    # print its header rule and page number onto the figure.
    standalone_tex = (
        head
        + context
        + "\n\\begin{document}\n\\thispagestyle{empty}\n"
        + env
        + "\n\\end{document}\n"
    )
    job = jobname or "fig"
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        tex_path = tmpdir / f"{job}.tex"
        tex_path.write_text(standalone_tex, encoding="utf-8")
        try:
            proc = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", f"{job}.tex"],
                cwd=str(tmpdir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PDFLATEX_TIMEOUT_SECONDS,
                check=False,
                env=proc_env,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "tikz: pdflatex timed out (%ss); leaving env as-is",
                _PDFLATEX_TIMEOUT_SECONDS,
            )
            return False
        except FileNotFoundError:
            logger.debug("tikz: pdflatex not on PATH; leaving envs as-is")
            return False
        pdf_path = tmpdir / f"{job}.pdf"
        if not pdf_path.is_file():
            # No PDF means a hard failure (missing package, parse error).
            log_tail = (proc.stdout or "")[-500:]
            logger.warning(
                "tikz: pdflatex produced no PDF (rc=%s). Log tail: %s",
                proc.returncode,
                log_tail,
            )
            return False
        if proc.returncode != 0:
            # pdflatex returns non-zero for harmless package warnings (e.g.
            # bookmark/hyperref load-order under standalone) yet still emits
            # a perfectly valid PDF — the figure's there, just the loader
            # complained. Log + proceed instead of throwing the PDF away.
            logger.debug(
                "tikz: pdflatex rc=%s but PDF produced; using it",
                proc.returncode,
            )
        # The body is one standalone box, so the figure is the LAST page —
        # class-only preamble commands (ACL's \setlength\titlebox{5cm},
        # elsarticle's \journal{…}) typeset stray text onto an extra first
        # page. A paper-sized page means a layout style overrode the crop,
        # and a page with no ink is a plot with no data: both would show the
        # reader a blank box, so they count as failures.
        try:
            with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
                page = doc.load_page(doc.page_count - 1)
                if _is_paper_sized(page.rect.width, page.rect.height):
                    logger.warning(
                        "tikz: render is a whole %.0fx%.0fpt page; leaving "
                        "env as-is", page.rect.width, page.rect.height,
                    )
                    return False
                pix = page.get_pixmap(dpi=dpi)
                if not _has_ink(pix):
                    logger.warning(
                        "tikz: render is blank (rc=%s); leaving env as-is",
                        proc.returncode,
                    )
                    return False
                pix.save(str(png_path))
        except Exception as exc:  # noqa: BLE001 — pymupdf bare exceptions
            logger.warning("tikz: rasterise failed: %s", exc)
            return False
    return True


def rasterize_tikz_figures(
    tex: str,
    *,
    preamble: str,
    out_dir: Path,
    dpi: int = 300,
) -> str:
    """Replace each TikZ environment in ``tex`` with a ``\\includegraphics``
    pointing at a rendered PNG.

    Parameters
    ----------
    tex:
        The flattened LaTeX body (already sentinel-marked / chunked is
        fine; the env detection doesn't touch sentinels).
    preamble:
        The paper's preamble, as returned by ``extract_latex``. Reused
        for the standalone compile so the figure inherits the paper's
        packages, ``\\usetikzlibrary`` declarations, and any
        preamble-level macros.
    out_dir:
        Where rendered PNGs land. Typically the paper's ``source/`` dir
        so the subsequent ``figures.py`` pass picks them up by relative
        path.
    dpi:
        Rasterisation resolution. Default 300 DPI — TikZ figures lean
        heavily on fine strokes (slashes in tree edges, small-font node
        text) that alias noticeably below ~250; 300 keeps file sizes
        reasonable (~1 MB for a survey roadmap) while staying crisp on
        2x-density displays.

    Returns the rewritten ``tex``. Any block that fails to compile is
    left as-is (no exception propagated) — the caller's pipeline must
    not block on pdflatex's idiosyncrasies. When ``pdflatex`` is absent
    the function is a no-op.
    """
    if shutil.which("pdflatex") is None:
        logger.debug("rasterize_tikz_figures: pdflatex unavailable; no-op")
        return tex
    if not _TIKZ_ENV_RE.search(tex):
        return tex

    out_dir.mkdir(parents=True, exist_ok=True)

    env_spans = _tikz_env_spans(tex)
    loop_spans = _foreach_spans(tex, env_spans)
    # Each unit is (start, end, is_loop); an env inside a loop belongs to it.
    units = [(s, e, True) for s, e in loop_spans] + [
        (s, e, False) for s, e in env_spans
        if not any(ls <= s < le for ls, le in loop_spans)
    ]
    units.sort()

    parts: list[str] = []
    last_end = 0
    for i, (start, end, is_loop) in enumerate(units, start=1):
        parts.append(tex[last_end:start])
        env = tex[start:end]
        png_name = f"tikz-fig-{i:03d}.png"
        png_path = out_dir / png_name
        success = _compile_tikz_to_png(
            env,
            preamble=preamble,
            body_prefix=tex[:start],
            png_path=png_path,
            dpi=dpi,
            varwidth=is_loop,
            jobname=_gnuplot_jobname(env, out_dir),
        )
        if success:
            parts.append(f"\\includegraphics{{{png_name}}}")
        elif is_loop:
            # A bare \foreach left for pandoc fails the WHOLE paper's HTML
            # (plain-text fallback); dropping this one figure keeps the rest.
            logger.warning(
                "tikz: \\foreach figure failed to compile; dropping it",
            )
        else:
            parts.append(env)
        last_end = end
    parts.append(tex[last_end:])
    return "".join(parts)


__all__ = ["rasterize_tikz_figures"]
