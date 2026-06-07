# Complex-Table Rasterization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render pandoc-incompatible complex LaTeX tables (`tabular*`, `tabularx`, and plain `tabular` containing `\multirow`/`\makecell`/`\multicolumn`+`\cmidrule`) as images in the Citation Canvas instead of letting them fall through pandoc as garbled raw text (arXiv:2602.20200's RoboTwin comparison table).

**Architecture:** A new module `table_figures.py` mirrors the proven `tikz_figures.py` pass: detect hostile table environments (env-depth-aware so nested `tabular` inside `tabular*` matches correctly), compile each as a `standalone` `pdflatex` document (table-bedrock packages + the paper's own preamble + body-prefix `\definecolor` macros), rasterize page 0 to PNG via `pymupdf`, and replace **only the grid env** with `\includegraphics{…}` — leaving the surrounding `table` float + `\caption` for pandoc to render as text. Wired at the same three render call sites as `rasterize_tikz_figures`, immediately after it. Per-table graceful fallback; whole-pass no-op when `pdflatex` is absent.

**Tech Stack:** Python 3.11, `pdflatex` (already a hard slide-pipeline dependency), `pymupdf` (already used by `tikz_figures.py`), pytest/ruff/mypy via `uv`.

---

## Reference: read before starting

- `backend/src/paperhub/pipelines/tikz_figures.py` — the template. The new module copies its structure (`_compile_*_to_png`, the `standalone` preamble approach, the graceful-fallback + pdflatex-absent-no-op contract, `pymupdf` rasterization at a DPI). **Do NOT modify this file** — write a focused, independent module so the working TikZ path can't regress.
- `backend/tests/test_renderer.py` — shows the `if shutil.which("pdflatex") is None: pytest.skip(...)` pattern for pdflatex-dependent integration tests.

## File structure

- **Create** `backend/src/paperhub/pipelines/table_figures.py` — the whole feature: detector, hostility classifier, snippet builder, compiler, orchestrator `rasterize_complex_tables`. One responsibility (LaTeX table → image), one file, mirrors `tikz_figures.py`.
- **Create** `backend/tests/test_table_figures.py` — unit tests (pure functions, no pdflatex) + integration tests (skipif no pdflatex).
- **Modify** `backend/src/paperhub/cli/rerender_html.py` — call `rasterize_complex_tables` after `rasterize_tikz_figures` (line ~127).
- **Modify** `backend/src/paperhub/pipelines/paper_pipeline.py` — same, at both render paths (lines ~333 and ~519).

---

## Task 0: Branch

- [ ] **Step 1: Create the feature branch off main**

```bash
cd d:/GitHub/PaperHub
git checkout main
git checkout -b feat/complex-table-rasterization
```

Note: the hotfix branch `fix/latex-parse-mainfile-tiktoken` is separate and ready to merge on its own. If it merges to `main` before this branch finishes, `git rebase main` this branch (both touch the render path but different files, so conflicts are unlikely). Flagged again at the end.

---

## Task 1: Hostility classifier

**Files:**
- Create: `backend/src/paperhub/pipelines/table_figures.py`
- Test: `backend/tests/test_table_figures.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_table_figures.py
from paperhub.pipelines.table_figures import _is_hostile


def test_starred_and_x_envs_are_hostile() -> None:
    assert _is_hostile("tabular*", "a & b \\\\")
    assert _is_hostile("tabularx", "a & b \\\\")


def test_plain_tabular_is_not_hostile() -> None:
    assert not _is_hostile("tabular", "a & b & c \\\\ \\midrule x & 1 & 2 \\\\")


def test_multirow_or_makecell_makes_plain_tabular_hostile() -> None:
    assert _is_hostile("tabular", "\\multirow{2}{*}{a} & b \\\\")
    assert _is_hostile("tabular", "\\makecell{a\\\\b} & c \\\\")


def test_multicolumn_alone_is_not_hostile_but_with_cmidrule_is() -> None:
    assert not _is_hostile("tabular", "\\multicolumn{2}{c}{a} \\\\")
    assert _is_hostile("tabular", "\\multicolumn{2}{c}{a} \\\\ \\cmidrule(lr){1-2}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_table_figures.py -q`
Expected: FAIL — `ModuleNotFoundError` / `cannot import name '_is_hostile'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/paperhub/pipelines/table_figures.py
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
import re

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_table_figures.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/table_figures.py backend/tests/test_table_figures.py
git commit -m "feat(renderer): add hostile-table classifier for rasterization"
```

---

## Task 2: Env-depth-aware table finder

**Files:**
- Modify: `backend/src/paperhub/pipelines/table_figures.py`
- Test: `backend/tests/test_table_figures.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_table_figures.py
from paperhub.pipelines.table_figures import _find_table_envs


def test_finds_a_simple_tabular() -> None:
    tex = "before \\begin{tabular}{cc}a & b\\\\\\end{tabular} after"
    envs = _find_table_envs(tex)
    assert len(envs) == 1
    start, end, name = envs[0]
    assert name == "tabular"
    assert tex[start:end] == "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"


def test_nested_tabular_inside_tabular_star_yields_one_outermost_env() -> None:
    tex = (
        "\\begin{tabular*}{\\textwidth}{cc}"
        "\\begin{tabular}{cc}x & y\\\\\\end{tabular}"
        " & z\\\\\\end{tabular*}"
    )
    envs = _find_table_envs(tex)
    assert len(envs) == 1
    start, end, name = envs[0]
    assert name == "tabular*"
    assert tex[start:end] == tex  # spans the whole outer tabular*


def test_two_sibling_tables_yield_two_envs() -> None:
    tex = (
        "\\begin{tabular}{c}a\\\\\\end{tabular}"
        "MID"
        "\\begin{tabularx}{\\linewidth}{c}b\\\\\\end{tabularx}"
    )
    envs = _find_table_envs(tex)
    assert [n for _, _, n in envs] == ["tabular", "tabularx"]


def test_unclosed_env_is_skipped() -> None:
    tex = "\\begin{tabular}{cc}a & b oops no end"
    assert _find_table_envs(tex) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_table_figures.py -k find -q`
Expected: FAIL — `cannot import name '_find_table_envs'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to backend/src/paperhub/pipelines/table_figures.py (after _is_hostile)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_table_figures.py -k find -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/table_figures.py backend/tests/test_table_figures.py
git commit -m "feat(renderer): env-depth-aware table-environment finder"
```

---

## Task 3: Standalone-snippet builder

**Files:**
- Modify: `backend/src/paperhub/pipelines/table_figures.py`
- Test: `backend/tests/test_table_figures.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_table_figures.py
from paperhub.pipelines.table_figures import _build_snippet


def test_snippet_has_bedrock_textwidth_and_document() -> None:
    snip = _build_snippet("\\begin{tabular}{c}a\\\\\\end{tabular}", preamble="", body_prefix="")
    assert "\\documentclass[border=10pt]{standalone}" in snip
    assert "\\usepackage{booktabs}" in snip
    assert "\\setlength{\\textwidth}{18cm}" in snip
    assert "\\begin{document}" in snip and "\\end{document}" in snip
    assert "\\begin{tabular}{c}a" in snip


def test_snippet_strips_sentinels() -> None:
    env = "\\begin{tabular}{c}aPHCHUNKANCHOR12END & b\\\\\\end{tabular}"
    snip = _build_snippet(env, preamble="", body_prefix="")
    assert "PHCHUNKANCHOR" not in snip


def test_snippet_drops_paper_documentclass_but_keeps_definecolor() -> None:
    preamble = "\\documentclass[11pt]{article}\n\\newcommand{\\dmodel}{d}"
    body_prefix = "intro \\definecolor{hl}{RGB}{0,119,255} more"
    snip = _build_snippet("\\begin{tabular}{c}\\dmodel\\\\\\end{tabular}",
                          preamble=preamble, body_prefix=body_prefix)
    assert "\\documentclass[11pt]{article}" not in snip   # paper's class removed
    assert "\\newcommand{\\dmodel}{d}" in snip            # author macro kept
    assert "\\definecolor{hl}{RGB}{0,119,255}" in snip    # body-prefix colour kept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_table_figures.py -k snippet -q`
Expected: FAIL — `cannot import name '_build_snippet'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to backend/src/paperhub/pipelines/table_figures.py

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_table_figures.py -k snippet -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/table_figures.py backend/tests/test_table_figures.py
git commit -m "feat(renderer): standalone table-snippet builder (textwidth + sentinel strip)"
```

---

## Task 4: Compile a table to PNG (pdflatex + pymupdf)

**Files:**
- Modify: `backend/src/paperhub/pipelines/table_figures.py`
- Test: `backend/tests/test_table_figures.py`

This step mirrors `tikz_figures._compile_tikz_to_png` exactly — isolated temp dir, `-interaction=nonstopmode`, 60 s timeout, accept `rc!=0` when a PDF is still produced, rasterize page 0 via pymupdf, graceful False on any failure.

- [ ] **Step 1: Write the failing test (integration; skips without pdflatex)**

```python
# append to backend/tests/test_table_figures.py
import shutil
from pathlib import Path

import pytest

from paperhub.pipelines.table_figures import _compile_table_to_png


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_compile_simple_table_produces_png(tmp_path: Path) -> None:
    png = tmp_path / "t.png"
    ok = _compile_table_to_png(
        "\\begin{tabular}{cc}\\toprule a & b\\\\ \\midrule 1 & 2\\\\ \\bottomrule\\end{tabular}",
        preamble="",
        body_prefix="",
        png_path=png,
        dpi=150,
    )
    assert ok is True
    assert png.is_file() and png.stat().st_size > 0


def test_compile_returns_false_when_pdflatex_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: None)
    # No pdflatex on PATH -> FileNotFoundError inside subprocess -> graceful False.
    monkeypatch.setattr(
        "paperhub.pipelines.table_figures.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    ok = _compile_table_to_png(
        "\\begin{tabular}{c}a\\\\\\end{tabular}",
        preamble="", body_prefix="", png_path=tmp_path / "x.png", dpi=150,
    )
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_table_figures.py -k compile -q`
Expected: FAIL — `cannot import name '_compile_table_to_png'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to backend/src/paperhub/pipelines/table_figures.py
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

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
        # rc!=0 with a PDF present is a harmless warning (e.g. overfull hbox);
        # the table rendered, so use it.
        try:
            with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
                doc.load_page(0).get_pixmap(dpi=dpi).save(str(png_path))
        except Exception as exc:  # noqa: BLE001 — pymupdf raises bare exceptions
            logger.warning("table: rasterise failed: %s", exc)
            return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_table_figures.py -k compile -q`
Expected: PASS (the skipif test runs if pdflatex is installed; the missing-pdflatex test always runs).

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/table_figures.py backend/tests/test_table_figures.py
git commit -m "feat(renderer): compile a table env to PNG via pdflatex + pymupdf"
```

---

## Task 5: Orchestrator `rasterize_complex_tables`

**Files:**
- Modify: `backend/src/paperhub/pipelines/table_figures.py`
- Test: `backend/tests/test_table_figures.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_table_figures.py
from paperhub.pipelines.table_figures import rasterize_complex_tables


def test_no_op_when_pdflatex_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: None)
    tex = "\\begin{tabular*}{\\textwidth}{cc}a & b\\\\\\end{tabular*}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex


def test_simple_tabular_is_left_unchanged(tmp_path, monkeypatch) -> None:
    # pdflatex "present" but no hostile env -> unchanged, compiler never called.
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: "/usr/bin/pdflatex")
    called = []
    monkeypatch.setattr("paperhub.pipelines.table_figures._compile_table_to_png",
                        lambda *a, **k: called.append(1) or True)
    tex = "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex
    assert called == []


def test_hostile_table_replaced_with_includegraphics(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: "/usr/bin/pdflatex")
    # Stub the compiler to "succeed" and create the PNG so we test the rewrite.
    def fake_compile(env_text, *, preamble, body_prefix, png_path, dpi):
        png_path.write_bytes(b"\x89PNG")
        return True
    monkeypatch.setattr("paperhub.pipelines.table_figures._compile_table_to_png", fake_compile)
    tex = "pre \\begin{tabular*}{\\textwidth}{cc}a & b\\\\\\end{tabular*} post"
    out = rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150)
    assert "\\includegraphics{table-fig-001.png}" in out
    assert "\\begin{tabular*}" not in out
    assert out.startswith("pre ") and out.endswith(" post")
    assert (tmp_path / "table-fig-001.png").is_file()


def test_compile_failure_leaves_env_in_place(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.table_figures.shutil.which", lambda _: "/usr/bin/pdflatex")
    monkeypatch.setattr("paperhub.pipelines.table_figures._compile_table_to_png", lambda *a, **k: False)
    tex = "\\begin{tabular*}{\\textwidth}{cc}a & b\\\\\\end{tabular*}"
    assert rasterize_complex_tables(tex, preamble="", out_dir=tmp_path, dpi=150) == tex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_table_figures.py -k "op or replaced or unchanged or in_place" -q`
Expected: FAIL — `cannot import name 'rasterize_complex_tables'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to backend/src/paperhub/pipelines/table_figures.py

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
    return "".join(parts)


__all__ = ["rasterize_complex_tables"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_table_figures.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Lint + type, then commit**

```bash
cd backend && uv run ruff check src/paperhub/pipelines/table_figures.py tests/test_table_figures.py && uv run mypy src/paperhub/pipelines/table_figures.py
git add backend/src/paperhub/pipelines/table_figures.py backend/tests/test_table_figures.py
git commit -m "feat(renderer): rasterize_complex_tables orchestrator + tests"
```

Expected: ruff "All checks passed!", mypy "Success: no issues found".

---

## Task 6: Wire into the three render call sites

**Files:**
- Modify: `backend/src/paperhub/cli/rerender_html.py` (~line 127)
- Modify: `backend/src/paperhub/pipelines/paper_pipeline.py` (~lines 333 and 519)

Each site currently reads (with site-specific `preamble`/`out_dir` arg values):
```python
marked = rasterize_tikz_figures(
    marked, preamble=<preamble>, out_dir=<dir>,
)
marked = strip_includegraphics_options(marked)
```
Insert a `rasterize_complex_tables` call between them, with the **same** `preamble`/`out_dir` args used by the adjacent `rasterize_tikz_figures` call at that site.

- [ ] **Step 1: rerender_html.py — add import**

Add to the imports (next to the tikz import at line 38):
```python
from paperhub.pipelines.table_figures import rasterize_complex_tables
```

- [ ] **Step 2: rerender_html.py — insert the call (after line ~129)**

```python
    marked = rasterize_tikz_figures(
        marked, preamble=preamble, out_dir=resource_dir,
    )
    # Rasterise pandoc-hostile tables (tabular*, \multirow, …) to images.
    marked = rasterize_complex_tables(
        marked, preamble=preamble, out_dir=resource_dir,
    )
    marked = strip_includegraphics_options(marked)
```

- [ ] **Step 3: paper_pipeline.py — add import (next to line 71)**

```python
from paperhub.pipelines.table_figures import rasterize_complex_tables
```

- [ ] **Step 4: paper_pipeline.py — insert at BOTH render paths**

At the first site (~line 333) and the second (~line 519), after each `rasterize_tikz_figures(... out_dir=source_path.parent)` call and before `strip_includegraphics_options`:
```python
        marked = rasterize_complex_tables(
            marked, preamble=ext.preamble, out_dir=source_path.parent,
        )
```
(Match the indentation of the surrounding `marked = ...` lines at each site — the second site is more deeply indented.)

- [ ] **Step 5: Verify wiring compiles + existing tests stay green**

Run: `cd backend && uv run ruff check src && uv run mypy src && uv run pytest -q`
Expected: ruff/mypy clean; pytest still green (1016 + the new table tests). No existing test should break — the new pass is a no-op on papers with no hostile tables (and on machines without pdflatex).

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/cli/rerender_html.py backend/src/paperhub/pipelines/paper_pipeline.py
git commit -m "feat(renderer): wire rasterize_complex_tables into the render paths"
```

---

## Task 7: Final gates + real verification

- [ ] **Step 1: Full backend gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 2: Real `:8000`-class verify (requires pdflatex)**

```bash
cd backend
# Find the paper_content id for 2602.20200:
uv run python -c "import sqlite3; print(sqlite3.connect('workspace/paperhub.db').execute(\"SELECT id FROM paper_content WHERE arxiv_id LIKE '2602.20200%'\").fetchone())"
# Re-render it (substitute the id printed above):
uv run paperhub-rerender-html --paper-content-id <id>
# Confirm the RoboTwin tabular* became an image and a PNG was produced:
grep -c "table-fig-" workspace/papers_cache/arxiv/2602.20200/source.html   # expect >= 1
ls workspace/papers_cache/arxiv/2602.20200/*/table-fig-*.png 2>/dev/null || ls workspace/papers_cache/arxiv/2602.20200/source/table-fig-*.png
```
Expected: at least one `table-fig-NNN.png` exists and `source.html` references it via a served `asset/` `<img>` (the existing figures pass externalises it). Simple tables in the same paper remain HTML `<table>`s.

- [ ] **Step 3: Human sign-off**

Ask the user to reload the Citation Canvas for 2602.20200 and confirm the RoboTwin comparison table now renders as a crisp image (caption still selectable text below it), and that simple tables elsewhere still render as HTML grids.

---

## Notes / follow-ups (out of scope)

- **Citation anchoring inside rasterized tables:** sentinels in a rasterized table are stripped, so a `[chunk:N]` into that table falls back to section-scroll (no exact highlight). Re-emitting the stripped sentinels immediately before the `\includegraphics` would restore approximate anchoring — a cheap future enhancement, deliberately deferred.
- **Bulk re-render:** after merge, run `paperhub-rerender-html` (no `--paper-content-id`) to apply table rasterization library-wide.
- **Branch ordering:** `fix/latex-parse-mainfile-tiktoken` (the render-bug hotfix branch) is independent and ready to merge. Merge it first, then `git rebase main` this branch before its own merge (different files, conflicts unlikely).
