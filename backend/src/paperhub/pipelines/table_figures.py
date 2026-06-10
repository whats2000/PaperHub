"""Make pandoc render complex LaTeX tables — as HTML where possible, else a figure.

Hard contract (never violated): a table ends up EITHER as a real HTML ``<table>``
OR as a rasterised figure — never silently dropped, and a table pandoc already
renders is never touched.

The constructs that make pandoc mis-handle a table are mechanical, so we *repair*
them before pandoc instead of guessing which tables are "hostile":

  1. ``\\cmidrule(l{..}r{..})`` trim specs leak into the cell as raw text
     (arXiv:1810.04805) -> strip the trim parenthetical.
  2. ``\\resizebox`` / ``\\scalebox`` / ``\\adjustbox`` wrappers are DROPPED by
     pandoc, vanishing the table inside (arXiv:2602.20200's LIBERO tables) ->
     unwrap the box, leaving the bare table so pandoc renders it as HTML.
  3. ``tabular*`` / ``tabularx`` / ``tabulary`` dump as a ``<div class="...">``
     raw-text block -> downgrade to a plain ``tabular`` (drop the width arg;
     map the ``X``/``Y`` flexible columns to ``l``) so pandoc renders them.

After these repairs the vast majority of tables render as selectable HTML with no
rasterisation at all. Only the residue pandoc STILL dumps (e.g. custom
``\\newcolumntype`` columns, or a tabular it simply chokes on) is rasterised: we
compile that env as a ``standalone`` document via ``pdflatex``, rasterise it to
PNG, and swap the env for ``\\includegraphics`` — leaving the surrounding
``table`` float + ``\\caption`` for pandoc. The residue is detected from pandoc's
ACTUAL output (the env's column-spec leaks into the dump), so a rendered table is
never rasterised.

Repairs are pure text and always applied; rasterising the residue needs
``pandoc`` (to detect) + ``pdflatex`` (to compile) and degrades gracefully when
either is absent (the table is left for pandoc, which at worst dumps it — never
worse than before).
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
from PIL import Image, ImageChops, ImageOps

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repair 1 — \cmidrule trim specs
# ---------------------------------------------------------------------------

# The (l{..}r{..}) parenthetical that shrinks a \cmidrule at column edges.
# pandoc can't parse it and leaks it into the cell as literal text
# ("(r0.2cm)1-3"); stripping to a bare \cmidrule{a-b} keeps the HTML <table>.
_CMIDRULE_TRIM_RE = re.compile(r"(\\cmidrule)\s*\([^)]*\)")


def strip_cmidrule_trim(tex: str) -> str:
    r"""Drop the ``(l{..}r{..})`` trim parenthetical from every ``\cmidrule``."""
    return _CMIDRULE_TRIM_RE.sub(r"\1", tex)


# ---------------------------------------------------------------------------
# Brace / env scanning helpers
# ---------------------------------------------------------------------------


def _matching_brace(s: str, open_idx: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``open_idx`` (escape-aware)."""
    depth, i = 0, open_idx
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _skip_group(s: str, i: int) -> int:
    """Skip whitespace then one ``{..}`` or ``[..]`` group; return the index
    just past it (or the original ``i`` if none is there)."""
    while i < len(s) and s[i] in " \t\n":
        i += 1
    if i < len(s) and s[i] == "{":
        end = _matching_brace(s, i)
        return end + 1 if end != -1 else i
    if i < len(s) and s[i] == "[":
        end = s.find("]", i)
        return end + 1 if end != -1 else i
    return i


# ---------------------------------------------------------------------------
# Repair 2 — unwrap width-fitting boxes around a tabular
# ---------------------------------------------------------------------------

_FITTING_RE = re.compile(r"\\(resizebox|scalebox|adjustbox)\b")
# Max unwrap passes — a defensive bound against a pathological input (each pass
# removes one box, real papers nest at most a handful).
_MAX_UNWRAP_PASSES = 200


def unwrap_table_boxes(tex: str) -> str:
    r"""Replace ``\resizebox{w}{h}{X}`` / ``\scalebox{f}{X}`` /
    ``\adjustbox{k}{X}`` with ``X`` whenever ``X`` contains a ``tabular`` — pandoc
    drops the whole box otherwise, vanishing the table. The box only scaled the
    table to fit a PDF page; in the width-flexible Canvas it is redundant.

    Boxes NOT wrapping a tabular (e.g. around a real figure) are left alone."""
    for _ in range(_MAX_UNWRAP_PASSES):
        unwrapped = False
        for m in _FITTING_RE.finditer(tex):
            j = m.end()
            nargs = 2 if m.group(1) == "resizebox" else 1
            for _ in range(nargs):
                j = _skip_group(tex, j)
            while j < len(tex) and tex[j] in " \t\n":
                j += 1
            if j >= len(tex) or tex[j] != "{":
                continue
            close = _matching_brace(tex, j)
            if close == -1:
                continue
            content = tex[j + 1 : close]
            if "\\begin{tabular" not in content and "\\begin{NiceTabular" not in content:
                continue
            tex = tex[: m.start()] + content + tex[close + 1 :]
            unwrapped = True
            break
        if not unwrapped:
            return tex
    return tex


# ---------------------------------------------------------------------------
# Repair 3 — downgrade width-fixed envs (tabular* / tabularx / tabulary)
# ---------------------------------------------------------------------------

_WIDTH_ENVS = ("tabular*", "tabularx", "tabulary")
_STAR_COL_RE = re.compile(r"\*\{(\d+)\}\{((?:[^{}]|\{[^{}]*\})*)\}")
_BANG_SEP_RE = re.compile(r"!\{(?:[^{}]|\{[^{}]*\})*\}")


# Custom column types — `\newcolumntype{X}[n]{body}` (array package). pandoc
# does NOT process these definitions, so a colspec using the custom letter
# (e.g. `P{30pt}` from `\newcolumntype{P}[1]{>{\centering}p{#1}}`, arXiv:
# 2404.07214) is unparseable and the WHOLE tabular dumps to raw text. We parse
# the definitions, rewrite each usage to its pandoc-renderable base column, and
# strip the definitions (the `#1` inside them also aborts pandoc's parse).
ColTypeMap = dict[str, tuple[int, str]]  # name -> (nargs, base column)
_NEWCOLTYPE_RE = re.compile(r"\\newcolumntype\{(.)\}")


def _newcolumntype_base(body: str, nargs: int) -> str:
    r"""The pandoc-renderable base column for a ``\newcolumntype`` body. A
    parameterised column (takes a width arg) -> ``p`` (paragraph); a fixed
    column -> its top-level alignment char (``c``/``l``/``r``), else ``l``."""
    if nargs >= 1:
        return "p"
    depth = 0
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0 and ch in "clr":
            return ch
    return "l"


def _parse_newcolumntype_defs(tex: str) -> ColTypeMap:
    r"""Map each single-char ``\newcolumntype{X}[n]{body}`` name to
    ``(nargs, base_column)``. Comment-aware; malformed defs are skipped."""
    defs: ColTypeMap = {}
    for m in _NEWCOLTYPE_RE.finditer(tex):
        if _is_commented(tex, m.start()):
            continue
        i = m.end()
        nargs = 0
        if i < len(tex) and tex[i] == "[":
            close = tex.find("]", i)
            if close != -1:
                try:
                    nargs = int(tex[i + 1 : close].strip() or "0")
                except ValueError:
                    nargs = 0
                i = close + 1
        while i < len(tex) and tex[i] in " \t\n":
            i += 1
        if i >= len(tex) or tex[i] != "{":
            continue
        end = _matching_brace(tex, i)
        if end == -1:
            continue
        defs[m.group(1)] = (nargs, _newcolumntype_base(tex[i + 1 : end], nargs))
    return defs


def _strip_newcolumntype_defs(tex: str) -> str:
    r"""Remove every ``\newcolumntype{X}[n]{body}`` definition (comment-aware).
    pandoc can't use them and the ``#1`` inside aborts its parse; usages are
    rewritten separately by :func:`_rewrite_custom_columns`."""
    spans: list[tuple[int, int]] = []
    for m in _NEWCOLTYPE_RE.finditer(tex):
        if _is_commented(tex, m.start()):
            continue
        i = m.end()
        if i < len(tex) and tex[i] == "[":
            close = tex.find("]", i)
            if close != -1:
                i = close + 1
        while i < len(tex) and tex[i] in " \t\n":
            i += 1
        if i < len(tex) and tex[i] == "{":
            end = _matching_brace(tex, i)
            if end != -1:
                spans.append((m.start(), end + 1))
                continue
        spans.append((m.start(), m.end()))  # malformed — drop the token only
    if not spans:
        return tex
    chars = list(tex)
    for s, e in sorted(spans, reverse=True):
        del chars[s:e]
    return "".join(chars)


def _rewrite_custom_columns(cols: str, custom: ColTypeMap) -> str:
    """Rewrite top-level custom-column usages in a colspec to their base column:
    a parameterised ``P{30pt}`` -> ``p{30pt}`` (keep the arg), a fixed ``C`` ->
    ``c``. Only depth-0 letters are touched, so a custom letter inside a
    ``>{..}`` group is left alone."""
    if not custom:
        return cols
    out: list[str] = []
    i, depth = 0, 0
    while i < len(cols):
        c = cols[i]
        if c == "{":
            depth += 1
            out.append(c)
            i += 1
            continue
        if c == "}":
            depth -= 1
            out.append(c)
            i += 1
            continue
        if depth == 0 and c in custom:
            nargs, base = custom[c]
            if nargs >= 1:
                j = i + 1
                while j < len(cols) and cols[j] in " \t":
                    j += 1
                if j < len(cols) and cols[j] == "{":
                    k = _matching_brace(cols, j)
                    if k != -1:
                        out.append(base)
                        out.append(cols[j : k + 1])  # keep the {width} arg
                        i = k + 1
                        continue
            out.append(base)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _normalize_colspec_for_plain(cols: str, custom: ColTypeMap | None = None) -> str:
    r"""Rewrite a colspec into the subset pandoc renders as a plain ``tabular``:

    * expand ``*{n}{X}`` column repeats (pandoc can't parse them -> dump);
    * replace ``!{\\vrule…}`` custom separators with ``|``;
    * rewrite ``\\newcolumntype`` custom columns (``custom`` map) to their base;
    * map the flexible ``X`` / ``Y`` columns (tabularx/tabulary) to ``l``.

    Only TOP-LEVEL ``X``/``Y`` (and custom letters) are mapped — letters inside
    ``{..}`` groups (``p{2cm}``, ``>{\\bfseries}``) are preserved."""
    cols = _STAR_COL_RE.sub(lambda m: m.group(2) * int(m.group(1)), cols)
    cols = _BANG_SEP_RE.sub("|", cols)
    cols = _rewrite_custom_columns(cols, custom or {})
    out, depth = [], 0
    for c in cols:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        out.append("l" if (depth == 0 and c in "XY") else c)
    return "".join(out)


def _rewrite_colspec_at(
    tex: str, brace_open: int, custom: ColTypeMap | None = None,
) -> tuple[str, int]:
    """Rewrite the colspec group whose ``{`` is at ``brace_open`` via
    :func:`_normalize_colspec_for_plain`; return ``(new_tex, new_close_index)``."""
    c_end = _matching_brace(tex, brace_open)
    if c_end == -1:
        return tex, brace_open
    cols = _normalize_colspec_for_plain(tex[brace_open + 1 : c_end], custom)
    new = tex[:brace_open] + "{" + cols + "}" + tex[c_end + 1 :]
    return new, brace_open + len(cols) + 1


def downgrade_width_tables(tex: str, custom: ColTypeMap | None = None) -> str:
    r"""Rewrite ``\begin{tabular*}{W}{cols}`` / ``tabularx`` / ``tabulary`` to a
    plain ``\begin{tabular}{cols}`` (dropping the width arg, normalising the
    colspec) and their ``\end{...}`` to ``\end{tabular}``. pandoc renders plain
    ``tabular`` reliably but dumps the width-fixed variants as raw text."""
    for name in _WIDTH_ENVS:
        bpat = re.compile(r"\\begin\{" + re.escape(name) + r"\}")
        while True:
            m = bpat.search(tex)
            if m is None:
                break
            width_end = _skip_group(tex, m.end())  # past {width}
            c_start = width_end
            while c_start < len(tex) and tex[c_start] in " \t\n":
                c_start += 1
            if c_start >= len(tex) or tex[c_start] != "{":
                # Malformed (no colspec) — neutralise the \begin to avoid an
                # infinite loop; leave the body for pandoc.
                tex = tex[: m.start()] + "\\begin{tabular}" + tex[m.end() :]
                continue
            c_end = _matching_brace(tex, c_start)
            cols = _normalize_colspec_for_plain(tex[c_start + 1 : c_end], custom)
            tex = tex[: m.start()] + "\\begin{tabular}{" + cols + "}" + tex[c_end + 1 :]
        tex = tex.replace("\\end{" + name + "}", "\\end{tabular}")
    return tex


_PLAIN_BEGIN_RE = re.compile(r"\\begin\{tabular\}\{")


def _normalize_plain_colspecs(tex: str, custom: ColTypeMap | None = None) -> str:
    """Apply the colspec normalisation to every plain ``\\begin{tabular}{...}`` —
    an originally-plain tabular can still carry ``*{n}{c}`` / ``!{..}`` / a
    custom ``\\newcolumntype`` letter that pandoc dumps on."""
    i = 0
    while True:
        m = _PLAIN_BEGIN_RE.search(tex, i)
        if m is None:
            return tex
        tex, i = _rewrite_colspec_at(tex, m.end() - 1, custom)


def repair_tables_for_pandoc(tex: str) -> str:
    """All pure-text repairs that let pandoc render a table as HTML."""
    tex = strip_cmidrule_trim(tex)
    tex = unwrap_table_boxes(tex)
    # Capture custom \newcolumntype columns BEFORE stripping their definitions,
    # so colspec usages (P{30pt} -> p{30pt}) can be rewritten to a base column
    # pandoc renders instead of dumping the whole tabular (arXiv:2404.07214).
    custom = _parse_newcolumntype_defs(tex)
    tex = _strip_newcolumntype_defs(tex)
    tex = downgrade_width_tables(tex, custom)
    return _normalize_plain_colspecs(tex, custom)


# ---------------------------------------------------------------------------
# Residue detection — envs pandoc STILL dumps after the repairs
# ---------------------------------------------------------------------------

_BEGIN_RE = re.compile(r"\\begin\{(tabular)\}")  # only plain tabular survives repair
_FIRST_SPAN_RE = re.compile(r"<span>([^<]*)</span>")
# Open/close of a <table> or a <div class="tabular...">, for depth-aware
# OUTERMOST-outcome extraction (pandoc nests a tabular's sub-tables).
_TAG_RE = re.compile(r"<(/?)(table|div)\b([^>]*)>")
_DUMP_REGION_RE = re.compile(r'<div class="tabular[^"]*">.*?</div>', re.S)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CONTENT_TOK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.]*")
# A dump must be ≥60% explained by ONE env's own cells before we rasterise that
# env — a rendered table's cells live in its <table>, not the dump, so it scores
# near zero (arXiv:2102.05918's two `l|rrrr` tables: the dumped Multi30K scores
# ~0.96, the rendered Spearman ~0.19). Comfortable margin.
_DUMP_MATCH_MIN = 0.6
_PANDOC_PROBE_TIMEOUT_SECONDS = 60


def _content_tokens(s: str) -> set[str]:
    return {
        t.lower()
        for t in _CONTENT_TOK_RE.findall(_HTML_TAG_RE.sub(" ", s))
        if len(t) >= 2
    }


def _env_content_tokens(env: str) -> set[str]:
    body = re.sub(r"\\[A-Za-z@]+\*?", " ", env)
    return {t.lower() for t in _CONTENT_TOK_RE.findall(body) if len(t) >= 2}


def _norm_colspec(cs: str) -> str:
    cs = re.sub(r"@\{(?:[^{}]|\{[^{}]*\})*\}", "", cs)
    cs = re.sub(r"[<>]\{(?:[^{}]|\{[^{}]*\})*\}", "", cs)
    cs = re.sub(r"\*\{(\d+)\}\{([^{}]*)\}", lambda m: m.group(2) * int(m.group(1)), cs)
    return re.sub(r"\s+", "", cs)


def _is_commented(tex: str, pos: int) -> bool:
    r"""True if an unescaped ``%`` precedes ``pos`` on the same line."""
    line_start = tex.rfind("\n", 0, pos) + 1
    i = line_start
    while i < pos:
        if tex[i] == "\\":
            i += 2
            continue
        if tex[i] == "%":
            return True
        i += 1
    return False


def _matching_end(tex: str, name: str, after: int) -> int:
    r"""Index just past the ``\end{name}`` matching the ``\begin{name}`` whose
    body starts at ``after`` (same-name nesting aware). -1 if unbalanced."""
    begin_tok, end_tok = "\\begin{" + name + "}", "\\end{" + name + "}"
    depth, i = 1, after
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


def _find_plain_tabulars(tex: str) -> list[tuple[int, int]]:
    r"""Every OUTERMOST, non-commented ``\begin{tabular}...\end{tabular}`` as
    ``(start, end)``. Run AFTER the repairs, so width-fixed envs are already
    plain ``tabular``."""
    envs: list[tuple[int, int]] = []
    i = 0
    while True:
        m = _BEGIN_RE.search(tex, i)
        if m is None:
            break
        end = _matching_end(tex, "tabular", m.end())
        if end == -1:
            i = m.end()
            continue
        if not _is_commented(tex, m.start()):
            envs.append((m.start(), end))
        i = end
    return envs


def _render_pandoc(tex: str) -> str | None:
    """Full-document pandoc render; None if pandoc is absent / fails / times out."""
    try:
        proc = subprocess.run(
            ["pandoc", "--from", "latex", "--to", "html5", "--wrap=preserve"],
            input=tex,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PANDOC_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _outermost_outcomes(html: str) -> list[tuple[str, str]]:
    r"""Ordered ``(kind, colspec)`` for each OUTERMOST table outcome, where
    ``kind`` is ``"table"`` (a rendered ``<table>``) or ``"dump"`` (a
    ``<div class="tabular...">`` raw-text block). ``colspec`` is the dump's leaked
    first ``<span>`` (``""`` for a table). Depth-aware so a tabular pandoc nests
    inside a rendered table counts once."""
    outcomes: list[tuple[str, str]] = []
    depth = 0
    kind: str | None = None
    region_start = 0
    for m in _TAG_RE.finditer(html):
        closing, tag, attrs = m.group(1), m.group(2), m.group(3)
        is_table = tag == "table"
        is_dump = tag == "div" and 'class="tabular' in attrs
        if not closing and (is_table or is_dump):
            if depth == 0:
                kind = "table" if is_table else "dump"
                region_start = m.end()
            depth += 1
        elif closing and tag in ("table", "div") and depth > 0:
            depth -= 1
            if depth == 0 and kind is not None:
                colspec = ""
                if kind == "dump":
                    sp = _FIRST_SPAN_RE.search(html[region_start : m.start()])
                    colspec = _norm_colspec(sp.group(1)) if sp else ""
                outcomes.append((kind, colspec))
                kind = None
    return outcomes


def _match_dumps_by_content(
    html: str, envs: list[tuple[int, int]], tex: str
) -> list[tuple[int, int]]:
    r"""Match each dump region to the env whose OWN cells fill it, by content.

    Used when the 1:1 order mapping is unavailable (env/outcome counts differ).
    pandoc dumps a table's cells as raw text, so the dumped env's tokens appear
    verbatim in its dump region; a RENDERED table's cells live in its ``<table>``,
    not the dump, so it scores near zero. We rasterise the env that supplies
    ≥``_DUMP_MATCH_MIN`` of a dump's tokens (best match, each env used once) —
    safe disambiguation even when two tables share a column-spec."""
    targets: list[tuple[int, int]] = []
    used: set[int] = set()
    env_tokens = [_env_content_tokens(tex[s:e]) for (s, e) in envs]
    for m in _DUMP_REGION_RE.finditer(html):
        dt = _content_tokens(m.group(0))
        if not dt:
            continue
        best_idx, best_score = -1, 0.0
        for idx, et in enumerate(env_tokens):
            if idx in used or not et:
                continue
            score = len(et & dt) / len(dt)  # fraction of the dump supplied by env
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx != -1 and best_score >= _DUMP_MATCH_MIN:
            used.add(best_idx)
            targets.append(envs[best_idx])
    return targets


def _residual_dump_envs(repaired: str) -> list[tuple[int, int]]:
    r"""Envs in the REPAIRED tex that pandoc STILL dumps as raw text.

    After the repairs no table can vanish (every box is unwrapped), so each
    OUTERMOST plain-tabular env produces exactly one HTML outcome — a ``<table>``
    or a dump — in document order. When the outcome count matches the env count
    we map 1:1 by order and rasterise the dumped ones. If the counts disagree
    (a rare nested/edge case) we match each dump to its env by CONTENT instead —
    a rendered table's cells aren't in the dump, so it can't be matched; only the
    genuinely-dumped env is rasterised. A rendered table is never targeted either
    way. Empty when pandoc is unavailable / the whole doc failed."""
    html = _render_pandoc(repaired)
    if html is None:
        return []
    outcomes = _outermost_outcomes(html)
    if not any(k == "dump" for k, _ in outcomes):
        return []
    envs = _find_plain_tabulars(repaired)
    if len(outcomes) == len(envs):
        return [(s, e) for (s, e), (kind, _cs) in zip(envs, outcomes, strict=True)
                if kind == "dump"]
    # Counts out of step (a rare nested/edge case) — disambiguate by content so a
    # column-spec collision (arXiv:2102.05918's two `l|rrrr` tables, one renders /
    # one dumps) can't rasterise the rendered one.
    logger.info(
        "table: %d plain-tabular envs vs %d outcomes; matching dumps by content",
        len(envs), len(outcomes),
    )
    return _match_dumps_by_content(html, envs, repaired)


# ---------------------------------------------------------------------------
# Standalone compile -> PNG (only for the residue)
# ---------------------------------------------------------------------------

# Sentinel token injected at ingest (pipelines/sentinels.py); strip from a
# snippet before compiling (it breaks pdflatex).
_SENTINEL_RE = re.compile(r"PHCHUNKANCHOR\d+END")
_DOCUMENTCLASS_RE = re.compile(r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}\s*")
_DEFINECOLOR_RE = re.compile(r"\\definecolor\{[^}]+\}\{[^}]+\}\{[^}]+\}")
# Conference / page-layout style packages emit a "page layout violates the X
# style" BANNER at \begin{document} (or force a full title page) when used
# outside their own documentclass — which then rasterises INSTEAD of the table
# (arXiv:2102.05918 ICML). A standalone table needs none of them (the bedrock
# preamble supplies the table packages), so strip them from the snippet.
_LAYOUT_PACKAGE_RE = re.compile(
    r"\\usepackage(?:\[[^\]]*\])?\{[^}]*"
    r"(?:icml|neurips|nips|iclr|colm|cvpr|iccv|eccv|wacv|aaai|ijcai|acl|emnlp|"
    r"naacl|coling|sigconf|acmart|geometry|fullpage|a4wide)[^}]*\}",
    re.IGNORECASE,
)

# A generous symmetric border (all sides) — every rasterised table must show
# whitespace on the left AND right, which is the visual proof it rendered FULLY
# (content flush to the image edge means it was clipped). \textwidth is
# deliberately HUGE so a wide table (e.g. arXiv:2407.15595's NiceTabular, whose
# \resizebox we unwrapped) typesets at its TRUE natural width with NO \hsize
# Overfull spilling past the page edge to be clipped; standalone then crops the
# page to that full natural width + border, so the whole table is captured with
# margins on both sides. The image may be wide — the Canvas scales it to fit.
# (We do NOT use adjustbox max-width: it scales nicematrix tables unreliably —
# fine on a narrow one, still clipping a wider sibling — arXiv:2407.15595.)
_TABLE_BEDROCK_PREAMBLE = r"""\documentclass[border=20pt]{standalone}
\usepackage{booktabs}
\usepackage{multirow}
\usepackage{makecell}
\usepackage{array}
\usepackage{tabularx}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{amsmath,amssymb,amsfonts}
\usepackage{graphicx}
\setlength{\textwidth}{80cm}
"""

_PDFLATEX_TIMEOUT_SECONDS = 60
# Standard full-page sizes (pt) a misbehaving conference style emits as a
# spurious banner / overflow page BEFORE the standalone-cropped table
# (arXiv:2102.05918 ICML, 2406.07524 / 2506.14038 NeurIPS). We rasterise the
# cropped content page, skipping these.
_FULL_PAGE_SIZES = {(612, 792), (595, 842)}

# The snippet inlines the paper's OWN preamble (for its macros/colours), which
# `\usepackage`s arbitrary font/symbol packages. A package present on the dev
# box (MiKTeX auto-installs missing .sty on demand) but ABSENT from a fixed
# TeX Live (Debian/Docker — e.g. `tgcursor.sty` from the `tex-gyre` package,
# dropped by `--no-install-recommends`) makes pdflatex Emergency-stop with no
# PDF, so the table silently degrades to a raw-text dump in deploy. pdflatex
# names the absent file as ``File `X' not found.`` — we stub it empty and retry
# so the table still rasterises (a missing FONT package just falls back to the
# default font in the rasterised image — cosmetic). We only stub TEXT input
# classes that trigger the fatal stop; a missing binary font (.tfm/.pfb) is
# non-fatal (pdftex substitutes) and an empty stub would corrupt it.
_MISSING_INPUT_RE = re.compile(
    r"File `([^']+\.(?:sty|tex|def|cfg|clo|cls|ldf|fd))' not found"
)
_MAX_COMPILE_ATTEMPTS = 8  # initial + up to 7 stub-and-retry passes
_MAX_RERUN_PASSES = 3  # nicematrix \CodeBefore + cross-refs need extra passes


def _missing_input_files(log: str) -> list[str]:
    r"""Absent text-input filenames named in a pdflatex log (``File `X' not
    found.``), in first-seen order, deduplicated. Used to stub-and-retry past a
    package missing from a fixed TeX Live (see ``_MISSING_INPUT_RE``)."""
    seen: dict[str, None] = {}
    for name in _MISSING_INPUT_RE.findall(log):
        seen.setdefault(name, None)
    return list(seen)


# --- class/style definitions a rasterised table cell may need ---------------
# A paper's custom documentclass (.cls) or style (.sty) often defines the
# colours used by \cellcolor/\rowcolor and the macros used inside cells
# (\slashNumbers, …). The standalone snippet DROPS the paper documentclass (its
# conference page-layout breaks `standalone`), so those definitions vanish:
# an undefined colour renders the cell BLACK, an undefined macro drops to its
# concatenated args (arXiv:2407.15595's NiceTabular — \cellcolor{redentropy}
# went black, \slashNumbers{a}{b}{c} became "abc"). We scan the source dir's
# .cls/.sty and re-inject the COLOURS (always — name->value, safe) plus the
# MACROS actually used in the env (targeted — never pulls unrelated internals).
_COLORLET_RE = re.compile(r"\\colorlet\{[^}]+\}\{[^}]+\}")
_MACRO_DEF_HEAD_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)"
    r"\*?\s*\{?\\([A-Za-z@]+)\}?"
)
_DEF_HEAD_RE = re.compile(r"\\def\s*\\([A-Za-z@]+)")
_MACRO_NAME_RE = re.compile(r"\\([A-Za-z]+)")


def _used_macro_names(env_text: str) -> set[str]:
    return set(_MACRO_NAME_RE.findall(env_text))


def _extract_used_macro_defs(body: str, used: set[str]) -> list[str]:
    r"""Full ``\newcommand``/``\def`` definitions in ``body`` whose macro name is
    in ``used``, extracted with brace-balancing (comment-aware, each name once)."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in (_MACRO_DEF_HEAD_RE, _DEF_HEAD_RE):
        for m in pat.finditer(body):
            name = m.group(1)
            if name not in used or name in seen or _is_commented(body, m.start()):
                continue
            j = m.end()
            while j < len(body) and body[j] in " \t":
                j += 1
            while j < len(body) and body[j] == "[":  # [nargs] / [default]
                k = body.find("]", j)
                if k == -1:
                    break
                j = k + 1
                while j < len(body) and body[j] in " \t":
                    j += 1
            if j < len(body) and body[j] == "{":
                end = _matching_brace(body, j)
                if end != -1:
                    out.append(body[m.start() : end + 1])
                    seen.add(name)
    return out


def _collect_class_definitions(source_dir: Path | None, env_text: str) -> list[str]:
    r"""Colour + used-macro definitions from the paper's ``.cls``/``.sty`` files,
    to inject into a standalone table snippet (see the note above). Empty when
    ``source_dir`` is None / has no class files."""
    if source_dir is None:
        return []
    used = _used_macro_names(env_text)
    defs: list[str] = []
    for aux in sorted(source_dir.glob("*.cls")) + sorted(source_dir.glob("*.sty")):
        try:
            text = aux.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        defs += _DEFINECOLOR_RE.findall(text)
        defs += _COLORLET_RE.findall(text)
        defs += _extract_used_macro_defs(text, used)
    return defs


def _build_snippet(
    env_text: str, *, preamble: str, body_prefix: str, source_dir: Path | None = None
) -> str:
    """Assemble a compilable standalone document for one table environment."""
    env_clean = _SENTINEL_RE.sub("", env_text)
    clean_preamble = _LAYOUT_PACKAGE_RE.sub("", _DOCUMENTCLASS_RE.sub("", preamble))
    parts: list[str] = [clean_preamble]
    for m in _DEFINECOLOR_RE.finditer(body_prefix):
        parts.append(m.group(0))
    # Colours + used macros the paper's .cls/.sty supply (lost with the dropped
    # documentclass) — so \cellcolor / \slashNumbers etc. resolve in the snippet.
    parts += _collect_class_definitions(source_dir, env_text)
    context = "\n".join(p for p in parts if p)
    return (
        _TABLE_BEDROCK_PREAMBLE
        + context
        + "\n\\begin{document}\n"
        + env_clean
        + "\n\\end{document}\n"
    )


def _is_blank_pixmap(pix: pymupdf.Pixmap) -> bool:
    """True if a rasterised page has no dark content (coarse grid sample)."""
    buf, w, h, n = pix.samples, pix.width, pix.height, pix.n
    for y in range(0, h, max(1, h // 60)):
        row = y * w * n
        for x in range(0, w, max(1, w // 60)):
            i = row + x * n
            if buf[i] + buf[i + 1] + buf[i + 2] < 600:
                return False
    return True


def _table_pixmap(doc: pymupdf.Document, dpi: int) -> pymupdf.Pixmap | None:
    """Rasterise the page holding the cropped table.

    A conference style can emit a spurious full-page banner / blank page before
    the ``standalone``-cropped table, so prefer a non-blank page that is NOT a
    standard full page (letter/A4); fall back to the first non-blank page."""
    fallback: pymupdf.Pixmap | None = None
    for i in range(doc.page_count):
        page = doc.load_page(i)  # type: ignore[no-untyped-call]
        pix: pymupdf.Pixmap = page.get_pixmap(dpi=dpi)
        if _is_blank_pixmap(pix):
            continue
        if fallback is None:
            fallback = pix
        size = (round(page.rect.width), round(page.rect.height))
        if size not in _FULL_PAGE_SIZES:
            return pix  # the cropped content page
    return fallback


# Uniform white margin added around the tight-cropped table (px), as a fraction
# of dpi (~2mm at 300dpi) so the table image always has whitespace on the left
# AND right — the visual proof it rendered fully (no x-edge content = not clipped).
def _table_margin_px(dpi: int) -> int:
    return max(16, round(dpi * 0.08))


# Cap the rasterised table image width. A genuinely wide table (the paper scaled
# it with \resizebox; we typeset at full natural width so nothing clips) can be
# ~70cm = 8000px at 300dpi — illegible + heavy. Scale the FULL (un-clipped) image
# down to this instead, so it stays complete AND bounded.
_TABLE_MAX_PX = 3600


def _crop_and_pad(pix: pymupdf.Pixmap, pad: int, max_px: int = _TABLE_MAX_PX) -> Image.Image:
    """Tight-crop the rasterised page to its non-white content, scale it DOWN if
    it is wider than ``max_px`` (a wide table stays complete, just smaller), then
    add a uniform white margin — so the image FITS the table width with equal
    left/right (and top/bottom) margins. Removes the standalone crop's asymmetric
    border (a wide table left-aligned in a huge \\textwidth otherwise lands flush
    against the right edge)."""
    mode = "RGBA" if pix.alpha else "RGB"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if mode != "RGB":
        img = img.convert("RGB")
    bbox = ImageChops.difference(
        img, Image.new("RGB", img.size, (255, 255, 255))
    ).getbbox()
    if bbox is not None:
        img = img.crop(bbox)
    if img.width > max_px:
        img = img.resize(
            (max_px, max(1, round(img.height * max_px / img.width))),
            Image.Resampling.LANCZOS,
        )
    return ImageOps.expand(img, border=pad, fill=(255, 255, 255))


def _compile_table_to_png(
    env_text: str,
    *,
    preamble: str,
    body_prefix: str,
    png_path: Path,
    dpi: int,
    source_dir: Path | None = None,
) -> bool:
    """Compile one table env to ``png_path``. Return True on success; any failure
    (timeout, pdflatex absent, no PDF, blank render) is logged and returns False
    so the caller leaves the env in place. ``source_dir`` (default: the PNG's own
    dir) is scanned for the paper's .cls/.sty colour + macro definitions."""
    standalone_tex = _build_snippet(
        env_text, preamble=preamble, body_prefix=body_prefix,
        source_dir=source_dir if source_dir is not None else png_path.parent,
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "tbl.tex").write_text(standalone_tex, encoding="utf-8")
        # Local class/style files (cvpr.sty, …) live in the paper's source dir
        # (= png_path.parent); point TEXINPUTS there (trailing pathsep keeps the
        # default texmf search path).
        env = dict(os.environ)
        prior = env.get("TEXINPUTS", "")
        env["TEXINPUTS"] = str(png_path.parent.resolve()) + os.pathsep + prior
        pdf_path = tmpdir / "tbl.pdf"
        # Compile, stubbing any text-input file pdflatex reports missing and
        # retrying — so a font/symbol package absent from a fixed TeX Live (the
        # deploy Debian image) can't silently drop the table to a raw-text dump.
        stubbed: set[str] = set()
        proc: subprocess.CompletedProcess[str] | None = None
        for _ in range(_MAX_COMPILE_ATTEMPTS):
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
            if pdf_path.is_file():
                break
            new_missing = [m for m in _missing_input_files(proc.stdout or "")
                           if m not in stubbed]
            if not new_missing:
                break  # no recoverable missing-file error — give up
            for name in new_missing:
                (tmpdir / name).write_text("", encoding="utf-8")
                stubbed.add(name)
            logger.info(
                "table: stubbed missing LaTeX input(s) %s; retrying compile",
                sorted(new_missing),
            )
        assert proc is not None  # loop ran at least once
        if not pdf_path.is_file():
            logger.warning(
                "table: pdflatex produced no PDF (rc=%s, stubbed=%s). Log tail: %s",
                proc.returncode,
                sorted(stubbed),
                (proc.stdout or "")[-500:],
            )
            return False
        # nicematrix \CodeBefore cell-colour positioning + cross-references need
        # extra pdflatex passes; rerun while the log asks for it (the PDF exists,
        # so any failure here just keeps the under-resolved one we have).
        for _ in range(_MAX_RERUN_PASSES):
            if "Rerun" not in (proc.stdout or ""):
                break
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
            except (subprocess.TimeoutExpired, FileNotFoundError):
                break
        if proc.returncode != 0:
            logger.debug("table: pdflatex rc=%s but PDF produced; using it", proc.returncode)
        try:
            with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
                pix = _table_pixmap(doc, dpi)
                if pix is None:
                    logger.warning("table: rendered image is blank; leaving env as-is")
                    return False
                _crop_and_pad(pix, _table_margin_px(dpi)).save(str(png_path))
        except Exception as exc:  # noqa: BLE001 — pymupdf raises bare exceptions
            logger.warning("table: rasterise failed: %s", exc)
            return False
    return True


# ---------------------------------------------------------------------------
# table/table* float -> figure when it now holds a rasterised image
# ---------------------------------------------------------------------------

_FLOAT_BEGIN_RE = re.compile(r"\\begin\{(table\*|table)\}")


def _convert_rasterized_table_floats(tex: str) -> str:
    r"""Rename a ``table``/``table*`` float to ``figure`` when it now contains a
    rasterised ``table-fig-`` image (pandoc drops a table float's caption + left-
    aligns it when no real tabular body survives; a ``figure`` float renders a
    centred, captioned ``<figure>``). Floats with a surviving real tabular are
    left for pandoc."""
    out: list[str] = []
    i = 0
    while True:
        m = _FLOAT_BEGIN_RE.search(tex, i)
        if m is None:
            out.append(tex[i:])
            break
        name = m.group(1)
        end = _matching_end(tex, name, m.end())
        if end == -1:
            out.append(tex[i : m.end()])
            i = m.end()
            continue
        out.append(tex[i : m.start()])
        block = tex[m.start() : end]
        if "\\includegraphics{table-fig-" in block:
            begin_tok, end_tok = "\\begin{" + name + "}", "\\end{" + name + "}"
            block = "\\begin{figure}" + block[len(begin_tok) :]
            if block.endswith(end_tok):
                block = block[: -len(end_tok)] + "\\end{figure}"
        out.append(block)
        i = end
    return "".join(out)


# ---------------------------------------------------------------------------
# nicematrix envs — pandoc has no support, so they ALWAYS dump → rasterise
# ---------------------------------------------------------------------------

# Order doesn't affect correctness (each \begin{NAME} requires the exact closing
# brace, so NiceTabular can't match NiceTabularX); listed widest-first for clarity.
_NICE_ENVS = ("NiceTabular*", "NiceTabularX", "NiceTabular", "NiceArray")


def _find_nice_envs(tex: str) -> list[tuple[int, int]]:
    r"""Outermost, non-commented nicematrix table envs (``\begin{NiceTabular}…``).
    pandoc can't render nicematrix, so these always dump as raw text — we
    rasterise them unconditionally (no pandoc round-trip needed to detect)."""
    spans: list[tuple[int, int]] = []
    for name in _NICE_ENVS:
        btok = "\\begin{" + name + "}"
        i = 0
        while True:
            b = tex.find(btok, i)
            if b == -1:
                break
            end = _matching_end(tex, name, b + len(btok))
            if end == -1:
                i = b + len(btok)
                continue
            if not _is_commented(tex, b):
                spans.append((b, end))
            i = end
    return spans


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and absorb any span nested in / overlapping an earlier one, so each
    region is rasterised exactly once (e.g. a ``NiceArray`` inside a
    ``NiceTabular``)."""
    out: list[tuple[int, int]] = []
    for s, e in sorted(set(spans)):
        if out and s < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def rasterize_complex_tables(
    tex: str, *, preamble: str, out_dir: Path, dpi: int = 300
) -> str:
    r"""Repair tables so pandoc renders them as HTML, then rasterise what it
    still can't: the residue it dumps AND every nicematrix env (pandoc has no
    nicematrix support, so a ``NiceTabular`` always dumps as raw text).

    Returns the rewritten LaTeX. The pure-text repairs (cmidrule-trim strip,
    fitting-box unwrap, width-env downgrade) are ALWAYS applied. When both
    ``pandoc`` and ``pdflatex`` are present, each target env is compiled to a PNG
    (with the paper's .cls/.sty colours + macros injected) and swapped for
    ``\includegraphics`` (its ``table``/``table*`` float becomes a ``figure`` so
    the caption survives). A rendered table is never rasterised; a compile
    failure leaves that env for pandoc.
    """
    tex = repair_tables_for_pandoc(tex)
    if shutil.which("pandoc") is None or shutil.which("pdflatex") is None:
        logger.debug(
            "rasterize_complex_tables: pandoc/pdflatex unavailable; repairs only"
        )
        return tex
    targets = _merge_spans(_find_nice_envs(tex) + _residual_dump_envs(tex))
    if not targets:
        return tex
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    last_end = 0
    for idx, (start, end) in enumerate(targets, start=1):
        parts.append(tex[last_end:start])
        png_name = f"table-fig-{idx:03d}.png"
        ok = _compile_table_to_png(
            tex[start:end],
            preamble=preamble,
            body_prefix=tex[:start],
            png_path=out_dir / png_name,
            dpi=dpi,
            source_dir=out_dir,
        )
        parts.append(f"\\includegraphics{{{png_name}}}" if ok else tex[start:end])
        last_end = end
    parts.append(tex[last_end:])
    return _convert_rasterized_table_floats("".join(parts))


__all__ = ["rasterize_complex_tables", "repair_tables_for_pandoc", "strip_cmidrule_trim"]
