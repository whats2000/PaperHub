"""Pre-rasterise complex LaTeX tables so pandoc can embed them as <img>.

Pandoc cannot parse the ``tabular*`` / ``tabularx`` environments at all (it
emits ``<div class="tabular*">`` and dumps the column spec + every &-separated
cell as raw text), and it mishandles ``\\multirow`` / ``\\makecell`` / dense
``\\multicolumn``+``\\cmidrule`` tables. arXiv:2602.20200's RoboTwin comparison
table (a 14-column ``tabular*`` with ``\\multirow`` headers) is the motivating
case. This module compiles each such table as a ``standalone`` document via
``pdflatex``, rasterises it to PNG, and rewrites the grid environment to
``\\includegraphics`` — leaving the surrounding ``table`` float + ``\\caption``
in place so pandoc still renders the caption as selectable text.

Mirrors ``tikz_figures.rasterize_tikz_figures``: ``pdflatex`` is already a hard
slide-pipeline dependency, failures are graceful (an un-compilable table is left
as-is), and the whole pass is a no-op when ``pdflatex`` is absent.
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

logger = logging.getLogger(__name__)

# A plain ``tabular`` is hostile only if its body uses constructs pandoc
# mishandles; ``tabular*`` / ``tabularx`` are hostile by environment.
_HOSTILE_BODY_RE = re.compile(r"\\multirow|\\makecell")
_MULTICOLUMN_RE = re.compile(r"\\multicolumn")
_CMIDRULE_RE = re.compile(r"\\cmidrule")


def _is_hostile(env_name: str, body: str) -> bool:
    """True if a table environment can't be reliably rendered by pandoc."""
    if env_name in ("tabular*", "tabularx"):
        return True
    if _HOSTILE_BODY_RE.search(body):
        return True
    return bool(_MULTICOLUMN_RE.search(body) and _CMIDRULE_RE.search(body))


# Match \begin{<name>} where <name> is a table family env. Order the
# alternation so the starred / x variants win over the bare "tabular".
_BEGIN_RE = re.compile(r"\\begin\{(tabular\*|tabularx|tabular)\}")


def _matching_end(tex: str, name: str, after: int) -> int:
    r"""Return the index just past the ``\end{name}`` matching the
    ``\begin{name}`` whose body starts at ``after``, counting same-name nesting.
    Returns -1 if unbalanced."""
    begin_tok = "\\begin{" + name + "}"
    end_tok = "\\end{" + name + "}"
    depth = 1
    i = after
    while i < len(tex):
        b = tex.find(begin_tok, i)
        e = tex.find(end_tok, i)
        if e == -1:
            return -1
        if b != -1 and b < e:
            depth += 1
            i = b + len(begin_tok)
        else:
            depth -= 1
            if depth == 0:
                return e + len(end_tok)
            i = e + len(end_tok)
    return -1


def _find_table_envs(tex: str) -> list[tuple[int, int, str]]:
    r"""Find every OUTERMOST tabular-family environment as ``(start, end,
    name)``. Env-depth-aware: a ``tabular`` nested inside a ``tabular*`` is part
    of the outer match, not returned separately (we jump past each outer env).
    Unbalanced begins are skipped."""
    envs: list[tuple[int, int, str]] = []
    i = 0
    while True:
        m = _BEGIN_RE.search(tex, i)
        if m is None:
            break
        name = m.group(1)
        end = _matching_end(tex, name, m.end())
        if end == -1:
            i = m.end()
            continue
        envs.append((m.start(), end, name))
        i = end  # skip the whole env so nested children aren't double-counted
    return envs


# Sentinel token injected at ingest (pipelines/sentinels.py). It is plain text
# that breaks pdflatex, so strip it from a snippet before compiling. The cited
# chunk then falls back to section-scroll in the Canvas (accepted tradeoff).
_SENTINEL_RE = re.compile(r"PHCHUNKANCHOR\d+END")

# Strip the paper's own \documentclass — our standalone class replaces it.
_DOCUMENTCLASS_RE = re.compile(r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}\s*")
# Colours defined inline in the body before the table (\cellcolor/\rowcolor).
_DEFINECOLOR_RE = re.compile(r"\\definecolor\{[^}]+\}\{[^}]+\}\{[^}]+\}")

# Packages a complex table tends to want. \setlength{\textwidth}{18cm} gives
# tabular*{\textwidth}{...\extracolsep{\fill}...} a concrete width to fill;
# the standalone class then crops the page to the actual table content.
_TABLE_BEDROCK_PREAMBLE = r"""\documentclass[border=10pt]{standalone}
\usepackage{booktabs}
\usepackage{multirow}
\usepackage{makecell}
\usepackage{array}
\usepackage{tabularx}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{amsmath,amssymb,amsfonts}
\usepackage{graphicx}
\setlength{\textwidth}{18cm}
"""


def _build_snippet(env_text: str, *, preamble: str, body_prefix: str) -> str:
    """Assemble a compilable standalone document for one table environment."""
    env_clean = _SENTINEL_RE.sub("", env_text)
    parts: list[str] = [_DOCUMENTCLASS_RE.sub("", preamble)]
    for m in _DEFINECOLOR_RE.finditer(body_prefix):
        parts.append(m.group(0))
    context = "\n".join(p for p in parts if p)
    return (
        _TABLE_BEDROCK_PREAMBLE
        + context
        + "\n\\begin{document}\n"
        + env_clean
        + "\n\\end{document}\n"
    )


_PDFLATEX_TIMEOUT_SECONDS = 60


def _compile_table_to_png(
    env_text: str,
    *,
    preamble: str,
    body_prefix: str,
    png_path: Path,
    dpi: int,
) -> bool:
    """Compile one table env to ``png_path``. Return True on success.

    pdflatex runs in an isolated temp dir; the PNG is written via pymupdf at
    ``dpi``. Any failure (timeout, pdflatex absent, no PDF, rasterise error) is
    logged and returned as False so the caller leaves the original env in place.
    """
    standalone_tex = _build_snippet(env_text, preamble=preamble, body_prefix=body_prefix)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "tbl.tex").write_text(standalone_tex, encoding="utf-8")
        # Local class/style files (cvpr.sty, fairmeta.cls, …) live in the
        # paper's source dir (= png_path.parent), not the isolated temp dir, so
        # the paper preamble's \usepackage{cvpr} would fail "File not found".
        # Point TEXINPUTS at that dir; the trailing os.pathsep keeps the default
        # texmf search path so standard packages still resolve.
        env = dict(os.environ)
        prior = env.get("TEXINPUTS", "")
        env["TEXINPUTS"] = str(png_path.parent.resolve()) + os.pathsep + prior
        try:
            proc = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "tbl.tex"],
                cwd=str(tmpdir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PDFLATEX_TIMEOUT_SECONDS,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "table: pdflatex timed out (%ss); leaving env as-is",
                _PDFLATEX_TIMEOUT_SECONDS,
            )
            return False
        except FileNotFoundError:
            logger.debug("table: pdflatex not on PATH; leaving env as-is")
            return False
        pdf_path = tmpdir / "tbl.pdf"
        if not pdf_path.is_file():
            logger.warning(
                "table: pdflatex produced no PDF (rc=%s). Log tail: %s",
                proc.returncode,
                (proc.stdout or "")[-500:],
            )
            return False
        if proc.returncode != 0:
            # rc!=0 with a PDF present is a harmless warning (e.g. overfull
            # hbox); the table rendered, so log + use it (mirrors tikz_figures).
            logger.debug(
                "table: pdflatex rc=%s but PDF produced; using it",
                proc.returncode,
            )
        try:
            with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
                doc.load_page(0).get_pixmap(dpi=dpi).save(str(png_path))
        except Exception as exc:  # noqa: BLE001 — pymupdf raises bare exceptions
            logger.warning("table: rasterise failed: %s", exc)
            return False
    return True


def rasterize_complex_tables(
    tex: str, *, preamble: str, out_dir: Path, dpi: int = 300
) -> str:
    r"""Replace each pandoc-hostile table environment in ``tex`` with an
    ``\includegraphics`` pointing at a rendered PNG.

    Parameters mirror ``rasterize_tikz_figures``: ``preamble`` is the paper's
    preamble (reused for the standalone compile), ``out_dir`` is where PNGs land
    (the paper's ``source/`` dir, so the figures pass externalises them), ``dpi``
    is the rasterisation resolution (300 keeps dense tables crisp).

    Only OUTERMOST hostile envs are rasterised; the surrounding ``table`` float +
    ``\caption`` are left for pandoc. Non-hostile ``tabular`` envs are untouched.
    Any compile failure leaves that env as-is; ``pdflatex`` absent -> no-op.
    """
    if shutil.which("pdflatex") is None:
        logger.debug("rasterize_complex_tables: pdflatex unavailable; no-op")
        return tex
    hostile = [(s, e, n) for (s, e, n) in _find_table_envs(tex) if _is_hostile(n, tex[s:e])]
    if not hostile:
        return tex
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    last_end = 0
    for idx, (start, end, _name) in enumerate(hostile, start=1):
        parts.append(tex[last_end:start])
        png_name = f"table-fig-{idx:03d}.png"
        ok = _compile_table_to_png(
            tex[start:end],
            preamble=preamble,
            body_prefix=tex[:start],
            png_path=out_dir / png_name,
            dpi=dpi,
        )
        parts.append(f"\\includegraphics{{{png_name}}}" if ok else tex[start:end])
        last_end = end
    parts.append(tex[last_end:])
    return _unwrap_fitting_boxes("".join(parts))


# Width-fitting wrappers (\resizebox{W}{H}{…}, \scalebox{f}{…},
# \adjustbox{key}{…}) commonly wrap wide tables. pandoc drops the macro AND its
# content, so a rasterised table left inside one vanishes from the HTML
# (arXiv:2602.20200's LIBERO tables). The table is now an image that the Canvas
# CSS scales to the panel width, so the box is redundant — unwrap it around OUR
# generated image (the controlled `table-fig-NNN.png` pattern only).
_FITTING_BOX_RE = re.compile(
    r"\\(?:resizebox|scalebox|adjustbox)\s*(?:\{[^{}]*\}){1,2}\s*"
    r"\{\s*(?:%[^\n]*\n\s*)?"
    r"(\\includegraphics\{table-fig-\d+\.png\})"
    r"\s*(?:%[^\n]*\n\s*)?\}",
    re.DOTALL,
)


def _unwrap_fitting_boxes(tex: str) -> str:
    """Strip a width-fitting box (\\resizebox/\\scalebox/\\adjustbox) that wraps
    only one of our rasterised-table images, leaving the bare
    ``\\includegraphics`` (pandoc would otherwise drop the whole box)."""
    return _FITTING_BOX_RE.sub(r"\1", tex)


__all__ = ["rasterize_complex_tables"]
