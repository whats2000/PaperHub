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
            if "\\begin{tabular" not in content:
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


def _normalize_colspec_for_plain(cols: str) -> str:
    r"""Rewrite a colspec into the subset pandoc renders as a plain ``tabular``:

    * expand ``*{n}{X}`` column repeats (pandoc can't parse them -> dump);
    * replace ``!{\\vrule…}`` custom separators with ``|``;
    * map the flexible ``X`` / ``Y`` columns (tabularx/tabulary) to ``l``.

    Only TOP-LEVEL ``X``/``Y`` are mapped — letters inside ``{..}`` groups
    (``p{2cm}``, ``>{\\bfseries}``) are preserved."""
    cols = _STAR_COL_RE.sub(lambda m: m.group(2) * int(m.group(1)), cols)
    cols = _BANG_SEP_RE.sub("|", cols)
    out, depth = [], 0
    for c in cols:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        out.append("l" if (depth == 0 and c in "XY") else c)
    return "".join(out)


def _rewrite_colspec_at(tex: str, brace_open: int) -> tuple[str, int]:
    """Rewrite the colspec group whose ``{`` is at ``brace_open`` via
    :func:`_normalize_colspec_for_plain`; return ``(new_tex, new_close_index)``."""
    c_end = _matching_brace(tex, brace_open)
    if c_end == -1:
        return tex, brace_open
    cols = _normalize_colspec_for_plain(tex[brace_open + 1 : c_end])
    new = tex[:brace_open] + "{" + cols + "}" + tex[c_end + 1 :]
    return new, brace_open + len(cols) + 1


def downgrade_width_tables(tex: str) -> str:
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
            cols = _normalize_colspec_for_plain(tex[c_start + 1 : c_end])
            tex = tex[: m.start()] + "\\begin{tabular}{" + cols + "}" + tex[c_end + 1 :]
        tex = tex.replace("\\end{" + name + "}", "\\end{tabular}")
    return tex


_PLAIN_BEGIN_RE = re.compile(r"\\begin\{tabular\}\{")


def _normalize_plain_colspecs(tex: str) -> str:
    """Apply the colspec normalisation to every plain ``\\begin{tabular}{...}`` —
    an originally-plain tabular can still carry ``*{n}{c}`` / ``!{..}`` that
    pandoc dumps on."""
    i = 0
    while True:
        m = _PLAIN_BEGIN_RE.search(tex, i)
        if m is None:
            return tex
        tex, i = _rewrite_colspec_at(tex, m.end() - 1)


def repair_tables_for_pandoc(tex: str) -> str:
    """All pure-text repairs that let pandoc render a table as HTML."""
    tex = strip_cmidrule_trim(tex)
    tex = unwrap_table_boxes(tex)
    tex = downgrade_width_tables(tex)
    return _normalize_plain_colspecs(tex)


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

# Border is asymmetric {left bottom right top}: a wide table flush against the
# page's left edge would otherwise clip; the extra left margin re-centres it.
_TABLE_BEDROCK_PREAMBLE = r"""\documentclass[border={34pt 10pt 10pt 10pt}]{standalone}
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

_PDFLATEX_TIMEOUT_SECONDS = 60
# Standard full-page sizes (pt) a misbehaving conference style emits as a
# spurious banner / overflow page BEFORE the standalone-cropped table
# (arXiv:2102.05918 ICML, 2406.07524 / 2506.14038 NeurIPS). We rasterise the
# cropped content page, skipping these.
_FULL_PAGE_SIZES = {(612, 792), (595, 842)}


def _build_snippet(env_text: str, *, preamble: str, body_prefix: str) -> str:
    """Assemble a compilable standalone document for one table environment."""
    env_clean = _SENTINEL_RE.sub("", env_text)
    clean_preamble = _LAYOUT_PACKAGE_RE.sub("", _DOCUMENTCLASS_RE.sub("", preamble))
    parts: list[str] = [clean_preamble]
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


def _compile_table_to_png(
    env_text: str,
    *,
    preamble: str,
    body_prefix: str,
    png_path: Path,
    dpi: int,
) -> bool:
    """Compile one table env to ``png_path``. Return True on success; any failure
    (timeout, pdflatex absent, no PDF, blank render) is logged and returns False
    so the caller leaves the env in place."""
    standalone_tex = _build_snippet(env_text, preamble=preamble, body_prefix=body_prefix)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "tbl.tex").write_text(standalone_tex, encoding="utf-8")
        # Local class/style files (cvpr.sty, …) live in the paper's source dir
        # (= png_path.parent); point TEXINPUTS there (trailing pathsep keeps the
        # default texmf search path).
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
            logger.debug("table: pdflatex rc=%s but PDF produced; using it", proc.returncode)
        try:
            with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
                pix = _table_pixmap(doc, dpi)
                if pix is None:
                    logger.warning("table: rendered image is blank; leaving env as-is")
                    return False
                pix.save(str(png_path))  # type: ignore[no-untyped-call]
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
# Orchestrator
# ---------------------------------------------------------------------------


def rasterize_complex_tables(
    tex: str, *, preamble: str, out_dir: Path, dpi: int = 300
) -> str:
    r"""Repair tables so pandoc renders them as HTML, then rasterise only the
    residue it still dumps.

    Returns the rewritten LaTeX. The pure-text repairs (cmidrule-trim strip,
    fitting-box unwrap, width-env downgrade) are ALWAYS applied — they turn
    vanished/dumped tables into real HTML tables with no rasterisation. When both
    ``pandoc`` (to detect the residue) and ``pdflatex`` (to compile it) are
    present, any env pandoc STILL dumps is compiled to a PNG and swapped for
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
    targets = _residual_dump_envs(tex)
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
        )
        parts.append(f"\\includegraphics{{{png_name}}}" if ok else tex[start:end])
        last_end = end
    parts.append(tex[last_end:])
    return _convert_rasterized_table_floats("".join(parts))


__all__ = ["rasterize_complex_tables", "repair_tables_for_pandoc", "strip_cmidrule_trim"]
