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
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

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
# \tikzstyle{name}=[opts] — opts may span lines.
_TIKZSTYLE_RE = re.compile(
    r"\\tikzstyle\{[^}]+\}\s*=\s*\[[^\]]*\]", re.DOTALL
)
# \tikzset{name/.style=...} — group can be multi-line; allow one level of
# nested braces.
_TIKZSET_RE = re.compile(
    r"\\tikzset\{(?:[^{}]|\{[^{}]*\})*\}", re.DOTALL
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


def _gather_tikz_context(preamble: str, body_prefix: str) -> str:
    """Build the standalone-doc preamble for one figure.

    Layers:
      - Paper's own preamble (minus ``\\documentclass``, which our
        standalone class replaces). Brings ``\\usepackage{tikz}``,
        ``\\usepackage{forest}``, ``\\usetikzlibrary{…}``, ``\\definecolor``,
        ``\\tikzstyle``, ``\\tikzset``, ``\\newcommand``, etc. — anything
        the figure could legitimately depend on.
      - ``\\definecolor`` / ``\\tikzstyle`` / ``\\tikzset`` declarations
        that appear in the body BEFORE the figure (surveys often define
        colours inline at the top of the document body, not the preamble
        — arXiv:2503.07137's ``\\definecolor{line-color}{RGB}{0,119,255}``
        sits in the body at flat line 20).
    """
    parts: list[str] = []
    # Paper preamble, minus \\documentclass.
    parts.append(_DOCUMENTCLASS_RE.sub("", preamble))
    # Body-prefix macros, in document order.
    body_macros: list[tuple[int, str]] = []
    for pat in (_DEFINECOLOR_RE, _TIKZSTYLE_RE, _TIKZSET_RE):
        for m in pat.finditer(body_prefix):
            body_macros.append((m.start(), m.group(0)))
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
) -> bool:
    """Compile one TikZ env to ``png_path``. Return True on success.

    pdflatex runs in an isolated temp dir so .aux/.log/.pdf droppings
    don't pollute the paper's source/ tree. The PNG is written via
    pymupdf at ``dpi``. Any failure (timeout, non-zero exit, rasterise
    error) is logged and returned as False — the caller leaves the
    original TikZ env in place.
    """
    context = _gather_tikz_context(preamble, body_prefix)
    standalone_tex = (
        _STANDALONE_PREAMBLE
        + context
        + "\n\\begin{document}\n"
        + env
        + "\n\\end{document}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        tex_path = tmpdir / "fig.tex"
        tex_path.write_text(standalone_tex, encoding="utf-8")
        try:
            proc = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "fig.tex"],
                cwd=str(tmpdir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PDFLATEX_TIMEOUT_SECONDS,
                check=False,
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
        pdf_path = tmpdir / "fig.pdf"
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
        try:
            with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
                pix = doc.load_page(0).get_pixmap(dpi=dpi)
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

    parts: list[str] = []
    last_end = 0
    for i, m in enumerate(_TIKZ_ENV_RE.finditer(tex), start=1):
        parts.append(tex[last_end:m.start()])
        env = m.group(0)
        png_name = f"tikz-fig-{i:03d}.png"
        png_path = out_dir / png_name
        body_prefix = tex[: m.start()]
        success = _compile_tikz_to_png(
            env,
            preamble=preamble,
            body_prefix=body_prefix,
            png_path=png_path,
            dpi=dpi,
        )
        if success:
            parts.append(f"\\includegraphics{{{png_name}}}")
        else:
            parts.append(env)
        last_end = m.end()
    parts.append(tex[last_end:])
    return "".join(parts)


__all__ = ["rasterize_tikz_figures"]
