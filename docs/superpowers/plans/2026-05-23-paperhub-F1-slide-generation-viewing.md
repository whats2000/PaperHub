# Plan F · Phase 1 — Slide Generation + Viewing (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a conference-ready multi-paper Beamer deck from a session's enabled references via a traced LangGraph Report Agent, persist it per session, and view it (filmstrip + slide + speaker note) in a new Slides panel.

**Architecture:** A new `agents/report_graph.py` subgraph (`sl_resolve→sl_plan→sl_sections→sl_assemble→sl_compile→sl_notes→sl_emit`) reuses LaTeX/Beamer/compile/version-history helpers copied + adapted from `reference/paper2slides-plus/src/`. A `decks` table (one per session) + workspace `chat_session/<sid>/slides/` hold the artefact. The chat endpoint gains a `slides` branch emitting a `deck` SSE event; new REST endpoints serve the PDF/tex/metadata; the frontend adds a `SlidesPanel` (react-pdf, reusing the Citation Canvas pattern) in the shared right-panel slot.

**Tech Stack:** Python 3.11 + `uv`, FastAPI, LangGraph, aiosqlite, LiteLLM adapter, `pdflatex` (Beamer), Pydantic; React 19 + TS + Vite + Zustand + `react-pdf`.

**Phase 1 scope (this file):** create-only generation, persistence, viewing. **Out of scope (Phase 2):** edit/recreate, version-history UI, presentation mode, Q&A-during-talk choreography.

**Spec:** SRS v2.18 — UC-4, FR-12, §III-3 Report Agent row, §III-5.3, `decks` table in §III-7.

**Conventions (from CLAUDE.md):** TDD (failing test → minimal impl → commit). From `backend/`: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`. From `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`. Conventional Commits; commit body wraps 72 cols; every commit ends with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`. Every copied reference file gets a provenance header: `# Adapted from reference/paper2slides-plus/src/<file> @ <commit>`.

---

## File Structure

**Backend — new:**
- `backend/src/paperhub/pipelines/slide_pipeline/__init__.py` — package exports.
- `backend/src/paperhub/pipelines/slide_pipeline/latex_helpers.py` — adapted from `latex_utils.py`.
- `backend/src/paperhub/pipelines/slide_pipeline/beamer_helpers.py` — adapted from `beamer_utils.py`.
- `backend/src/paperhub/pipelines/slide_pipeline/compile.py` — adapted from `compiler.py` (LLM-revise loop rewired to `LlmAdapter`).
- `backend/src/paperhub/pipelines/slide_pipeline/history.py` — adapted `VersionHistory`, re-keyed to `session_id`.
- `backend/src/paperhub/pipelines/slide_pipeline/assemble.py` — preamble + `ADDITIONAL.tex` merge + `\graphicspath` (new code).
- `backend/src/paperhub/db/decks.py` — `decks` row CRUD.
- `backend/src/paperhub/agents/report_pipeline.py` — `plan_deck`, `generate_section`, `generate_notes` (traced pipeline fns).
- `backend/src/paperhub/agents/report_graph.py` — the subgraph + `ReportDeps` + `build_report_subgraph`.
- `backend/src/paperhub/api/decks.py` — REST router.
- `backend/src/paperhub/llm/prompts/slides_plan_v1.yaml`, `slides_section_v1.yaml`, `slides_notes_v1.yaml`.

**Backend — modified:**
- `backend/src/paperhub/db/schema.sql` — add `decks` table.
- `backend/src/paperhub/db/migrate.py` — idempotent `decks` create.
- `backend/src/paperhub/config.py` — `Settings` report model tiers + a `pdflatex` resolver.
- `backend/src/paperhub/models/domain.py` — `SlidePlan`, `PlannedSection`, `AgentState` deck fields.
- `backend/src/paperhub/agents/graph.py` — replace `_stub_slides` with the real subgraph.
- `backend/src/paperhub/api/chat.py` — `report_stream` shim + `slides` branch + `deck` SSE event.
- `backend/src/paperhub/app.py` — register `decks` router.

**Frontend — new:**
- `frontend/src/store/slides.ts` — slides store slice.
- `frontend/src/hooks/useDeckSync.ts` — refresh deck on session change.
- `frontend/src/components/slides/SlidesPanel.tsx` — filmstrip + slide + note.
- `frontend/src/components/slides/DeckChip.tsx` — assistant-message deck chip.

**Frontend — modified:**
- `frontend/src/lib/api.ts` — deck API functions.
- `frontend/src/types/domain.ts` — `DeckMeta` + `slides`-message field; add `memory` to `Intent`.
- `frontend/src/hooks/useChatStream.ts` — handle `deck` event.
- `frontend/src/pages/ChatPage.tsx` — mount `SlidesPanel` in the shared slot; toggle logic.
- `frontend/src/components/chat/Composer.tsx` — activate the Slides button.

**Scripts:**
- `backend/scripts/smoke_slides.ps1` — end-to-end mocked-LLM smoke.

---

## Task 1: `decks` table + migration

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`
- Modify: `backend/src/paperhub/db/migrate.py`
- Test: `backend/tests/test_decks_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_decks_schema.py
import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_decks_table_exists_with_unique_session(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await migrated_db.execute(
        "INSERT INTO decks (session_id, tex_path, page_count, theme, contributing_paper_ids_json) "
        "VALUES (1, 'slides/deck.tex', 0, 'metropolis', '[]')"
    )
    await migrated_db.commit()
    # UNIQUE(session_id): a second insert for the same session must fail
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO decks (session_id, tex_path, page_count, theme, contributing_paper_ids_json) "
            "VALUES (1, 'slides/other.tex', 0, 'metropolis', '[]')"
        )
        await migrated_db.commit()


@pytest.mark.asyncio
async def test_decks_status_check(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO decks (session_id, tex_path, page_count, theme, contributing_paper_ids_json, status) "
            "VALUES (1, 'x', 0, 'metropolis', '[]', 'bogus')"
        )
        await migrated_db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_decks_schema.py -v`
Expected: FAIL — `no such table: decks`.

- [ ] **Step 3: Add the table to `schema.sql`**

Append after the `memories` block in `backend/src/paperhub/db/schema.sql` (before the FTS virtual tables):

```sql
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    tex_path TEXT NOT NULL,
    pdf_path TEXT,
    speaker_notes_json TEXT,
    plan_json TEXT,
    page_count INTEGER NOT NULL DEFAULT 0,
    theme TEXT NOT NULL DEFAULT 'metropolis',
    contributing_paper_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (session_id)
);
```

- [ ] **Step 4: Add an idempotent migration**

In `backend/src/paperhub/db/migrate.py`, inside `apply_schema` after the existing column-add blocks (the `executescript(sql)` already runs the `CREATE TABLE IF NOT EXISTS`, so no extra code is needed for a fresh table — but add a guard comment so future column-adds have a home):

```python
    # decks (v2.18, Plan F): created by schema.sql's CREATE TABLE IF NOT EXISTS.
    # Future column-adds go here, mirroring the chat_sessions.deleted_at pattern.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_decks_schema.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/db/schema.sql backend/src/paperhub/db/migrate.py backend/tests/test_decks_schema.py
git commit -m "feat(db): add decks table (one current deck per session)"
```

---

## Task 2: Copy + adapt LaTeX/Beamer/compile/history helpers

These are verbatim copies from `reference/paper2slides-plus/src/` with a provenance header and the minimal edits noted. **Do not rewrite the logic** — copy the file content, then apply the listed adaptations.

**Files:**
- Create: `backend/src/paperhub/pipelines/slide_pipeline/__init__.py`
- Create: `backend/src/paperhub/pipelines/slide_pipeline/latex_helpers.py`
- Create: `backend/src/paperhub/pipelines/slide_pipeline/beamer_helpers.py`
- Create: `backend/src/paperhub/pipelines/slide_pipeline/history.py`
- Test: `backend/tests/test_slide_helpers.py`

- [ ] **Step 1: Find the reference commit hash for provenance**

Run: `cd reference/paper2slides-plus; git rev-parse --short HEAD` (if it's a submodule/clone). If not a git repo, use `@ vendored-2026-05` as the marker. Record the value as `<REF>`.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_slide_helpers.py
from paperhub.pipelines.slide_pipeline.latex_helpers import (
    extract_definitions_and_usepackage_lines,
    build_additional_tex,
    sanitize_frametitles,
)
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
    get_frame_by_number,
    replace_frame_in_beamer,
)


def test_extract_defs_and_build_additional() -> None:
    src = r"""\documentclass{article}
\usepackage{amsmath}
\newcommand{\bx}{\mathbf{x}}
\DeclareMathOperator{\softmax}{softmax}
\begin{document}\end{document}"""
    defs = extract_definitions_and_usepackage_lines(src)
    add = build_additional_tex(defs)
    assert "\\newcommand{\\bx}" in add
    assert "\\DeclareMathOperator{\\softmax}" in add


def test_frame_roundtrip() -> None:
    beamer = (
        "\\documentclass{beamer}\n\\begin{document}\n"
        "\\begin{frame}{A}\\end{frame}\n"
        "\\begin{frame}{B}\\end{frame}\n"
        "\\end{document}\n"
    )
    frames = extract_frames_from_beamer(beamer)
    assert len(frames) == 2
    assert frames[0][0] == 1  # 1-indexed frame number
    f2 = get_frame_by_number(beamer, 2)
    assert f2 is not None and "{B}" in f2
    out = replace_frame_in_beamer(beamer, 2, "\\begin{frame}{B2}\\end{frame}")
    assert out is not None and "{B2}" in out


def test_sanitize_frametitles_escapes_ampersand() -> None:
    assert "\\&" in sanitize_frametitles("\\frametitle{Cats & Dogs}")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_slide_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: paperhub.pipelines.slide_pipeline`.

- [ ] **Step 4: Create the package + copy the helpers**

Create `backend/src/paperhub/pipelines/slide_pipeline/__init__.py`:
```python
"""Slide pipeline (Plan F) — Beamer deck generation from enabled references.

LaTeX/Beamer/compile/history helpers are copied + adapted from
reference/paper2slides-plus/src/; orchestration is new LangGraph code.
"""
```

Copy `reference/paper2slides-plus/src/latex_utils.py` → `latex_helpers.py`. Prepend:
```python
# Adapted from reference/paper2slides-plus/src/latex_utils.py @ <REF>
# Original project: https://github.com/whats2000/paper2slides-plus (MIT).
```
Keep every function verbatim: `extract_definitions_and_usepackage_lines`, `build_additional_tex`, `save_additional_tex`, `save_latex_source`, `load_latex_source`, `add_additional_tex`, `sanitize_frametitles`. No logic changes.

Copy `reference/paper2slides-plus/src/beamer_utils.py` → `beamer_helpers.py` with the same provenance header. Keep every function verbatim.

Copy `reference/paper2slides-plus/src/history.py` → `history.py` with the provenance header, then **adapt**: the public surface stays identical (`VersionHistory(workspace_dir)`, `save_version`, `list_versions`, `restore_version`, `delete_version`, `get_version_by_filename`, `has_history`, `clear_history`), but change `__init__` to take only `workspace_dir: str | Path` (drop the `paper_id` arg and the `source/{paper_id}/` default — Phase-1 callers always pass an explicit `<sid>/slides/` dir). Update `get_history_manager(workspace_dir)` accordingly. Replace internal `slides.tex` path references with a `slides_tex_path` argument already passed by `restore_version` (unchanged).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_slide_helpers.py -v`
Expected: PASS.

- [ ] **Step 6: Lint/type the new files**

Run: `cd backend; uv run ruff check src/paperhub/pipelines/slide_pipeline; uv run mypy src/paperhub/pipelines/slide_pipeline`
Fix any import/type errors (the reference uses `logging`, `Optional`, etc. — add `from __future__ import annotations` if mypy flags old-style `Optional`).

- [ ] **Step 7: Commit**

```bash
git add backend/src/paperhub/pipelines/slide_pipeline/ backend/tests/test_slide_helpers.py
git commit -m "feat(slides): port latex/beamer/history helpers from paper2slides-plus"
```

---

## Task 3: Compile-fix loop wired to the `LlmAdapter`

The reference `try_compile_with_fixes` calls OpenAI directly via `call_llm`. We keep its compile + chktex + retry skeleton but inject a `revise` callback so it uses our `LlmAdapter` + prompt registry.

**Files:**
- Create: `backend/src/paperhub/pipelines/slide_pipeline/compile.py`
- Test: `backend/tests/test_slide_compile.py`

- [ ] **Step 1: Write the failing test** (uses a fake compiler so no real `pdflatex` needed)

```python
# backend/tests/test_slide_compile.py
from pathlib import Path
import pytest
from paperhub.pipelines.slide_pipeline.compile import compile_with_revise, CompileResult


@pytest.mark.asyncio
async def test_compile_success_first_try(tmp_path: Path, monkeypatch) -> None:
    workdir = tmp_path / "slides"
    workdir.mkdir()
    (workdir / "deck.tex").write_text("\\documentclass{beamer}\\begin{document}\\end{document}")

    # Fake pdflatex: write a deck.pdf and return rc=0
    def fake_run(cmd, cwd=None, **kw):
        Path(cwd, "deck.pdf").write_bytes(b"%PDF-1.4 fake")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", fake_run)

    async def never_revise(log: str, tex: str) -> str:  # should not be called
        raise AssertionError("revise called on success")

    res: CompileResult = await compile_with_revise(
        tex=(workdir / "deck.tex").read_text(),
        workdir=workdir,
        tex_name="deck.tex",
        revise=never_revise,
        max_retries=2,
    )
    assert res.ok is True
    assert res.attempts == 1
    assert (workdir / "deck.pdf").exists()


@pytest.mark.asyncio
async def test_compile_revises_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    workdir = tmp_path / "slides"
    workdir.mkdir()
    calls = {"n": 0}

    def fake_run(cmd, cwd=None, **kw):
        import subprocess
        calls["n"] += 1
        if calls["n"] == 1:  # first pdflatex fails (no pdf)
            return subprocess.CompletedProcess(cmd, 1, "! LaTeX Error", "")
        Path(cwd, "deck.pdf").write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", fake_run)

    async def revise(log: str, tex: str) -> str:
        return tex + "\n% fixed"

    res = await compile_with_revise(
        tex="\\documentclass{beamer}\\begin{document}\\end{document}",
        workdir=workdir, tex_name="deck.tex", revise=revise, max_retries=2,
    )
    assert res.ok is True
    assert res.attempts == 2
    assert "% fixed" in res.tex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_slide_compile.py -v`
Expected: FAIL — module/symbol not found.

- [ ] **Step 3: Implement `compile.py`**

```python
# backend/src/paperhub/pipelines/slide_pipeline/compile.py
"""Beamer compile-with-revise loop.

Skeleton adapted from reference/paper2slides-plus/src/compiler.py @ <REF>
(MIT); the LLM-revise step is injected as a callback so this module stays
adapter-agnostic (the Report Agent passes a closure over the LlmAdapter).
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from paperhub.pipelines.slide_pipeline.latex_helpers import sanitize_frametitles

ReviseFn = Callable[[str, str], Awaitable[str]]  # (pdflatex_log, current_tex) -> fixed_tex

PDFLATEX = shutil.which("pdflatex") or "pdflatex"


@dataclass
class CompileResult:
    ok: bool
    attempts: int
    tex: str
    log: str
    page_count: int


def _run_pdflatex(tex_name: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    cmd = [PDFLATEX, "-interaction=nonstopmode", tex_name]
    return subprocess.run(  # noqa: S603 — fixed binary, sandboxed workdir
        cmd, cwd=str(workdir), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )


def _page_count(pdf: Path) -> int:
    # Cheap: count "/Type /Page" occurrences is unreliable; use pypdf if available,
    # else fall back to 0 (page_count is also derivable client-side from numPages).
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except Exception:
        return 0


async def compile_with_revise(
    *, tex: str, workdir: Path, tex_name: str, revise: ReviseFn, max_retries: int = 3,
) -> CompileResult:
    workdir.mkdir(parents=True, exist_ok=True)
    current = sanitize_frametitles(tex)
    last_log = ""
    pdf_path = workdir / Path(tex_name).with_suffix(".pdf").name
    for attempt in range(1, max_retries + 2):
        (workdir / tex_name).write_text(current, encoding="utf-8")
        if pdf_path.exists():
            pdf_path.unlink()
        try:
            proc = _run_pdflatex(tex_name, workdir)
            last_log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            last_log = f"pdflatex timed out: {exc}"
            proc = subprocess.CompletedProcess([PDFLATEX], 1, "", last_log)
        if proc.returncode == 0 or pdf_path.exists():
            return CompileResult(True, attempt, current, last_log, _page_count(pdf_path))
        if attempt > max_retries:
            break
        # Trim the log to the last ~4000 chars of error context for the LLM.
        current = sanitize_frametitles(await revise(last_log[-4000:], current))
    return CompileResult(False, max_retries + 1, current, last_log, 0)
```

Add `pypdf` to deps: `cd backend; uv add pypdf`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_slide_compile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/slide_pipeline/compile.py backend/tests/test_slide_compile.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(slides): adapter-agnostic compile-with-revise loop"
```

---

## Task 4: Deck assembly (preamble + ADDITIONAL.tex merge + graphicspath)

**Files:**
- Create: `backend/src/paperhub/pipelines/slide_pipeline/assemble.py`
- Test: `backend/tests/test_slide_assemble.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_slide_assemble.py
from paperhub.pipelines.slide_pipeline.assemble import assemble_deck, AssembleInput


def test_assemble_includes_graphicspath_and_macros_and_frames() -> None:
    out = assemble_deck(AssembleInput(
        title="MoE Routing: A Comparison",
        theme="metropolis",
        additional_tex_macros=["\\newcommand{\\bx}{\\mathbf{x}}"],
        cache_source_dirs=["/ws/papers_cache/arxiv/2403.01234/source", "/ws/papers_cache/arxiv/2401.05678/source"],
        frames=["\\begin{frame}{Intro}\\end{frame}", "\\begin{frame}{Method}\\end{frame}"],
    ))
    assert "\\usetheme{metropolis}" in out
    assert "\\graphicspath{ {/ws/papers_cache/arxiv/2403.01234/source/} {/ws/papers_cache/arxiv/2401.05678/source/} }" in out
    assert "\\newcommand{\\bx}" in out
    assert "{Intro}" in out and "{Method}" in out
    assert out.strip().endswith("\\end{document}")
    assert "\\title{MoE Routing: A Comparison}" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_slide_assemble.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `assemble.py`**

```python
# backend/src/paperhub/pipelines/slide_pipeline/assemble.py
"""Assemble a Beamer deck from generated section frames (new code).

Writes the preamble (theme + ADDITIONAL.tex), title frame, all section frames,
and a single \\graphicspath spanning every contributing paper's cache source
dir (SRS v2.18 §III-5.3 step 4a). Figures are never copied into the session dir.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AssembleInput:
    title: str
    theme: str
    additional_tex_macros: list[str]
    cache_source_dirs: list[str]
    frames: list[str]


def build_additional_block(macros: list[str]) -> str:
    if not macros:
        return ""
    return "\n".join(macros)


def build_graphicspath(cache_source_dirs: list[str]) -> str:
    if not cache_source_dirs:
        return ""
    dirs = " ".join("{" + d.rstrip("/") + "/}" for d in cache_source_dirs)
    return f"\\graphicspath{{ {dirs} }}"


def assemble_deck(inp: AssembleInput) -> str:
    parts: list[str] = [
        "\\documentclass{beamer}",
        f"\\usetheme{{{inp.theme}}}",
        "\\usepackage{graphicx}",
        "\\usepackage{booktabs}",
        "\\usepackage{amsmath,amssymb}",
        build_graphicspath(inp.cache_source_dirs),
        build_additional_block(inp.additional_tex_macros),
        f"\\title{{{inp.title}}}",
        "\\begin{document}",
        "\\maketitle",
        *inp.frames,
        "\\end{document}",
    ]
    return "\n".join(p for p in parts if p) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_slide_assemble.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/slide_pipeline/assemble.py backend/tests/test_slide_assemble.py
git commit -m "feat(slides): deck assembly with graphicspath + macro merge"
```

---

## Task 5: Domain models — `PlannedSection`, `SlidePlan`, AgentState fields

**Files:**
- Modify: `backend/src/paperhub/models/domain.py`
- Test: `backend/tests/test_slide_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_slide_models.py
from paperhub.models.domain import SlidePlan, PlannedSection


def test_slide_plan_parses() -> None:
    plan = SlidePlan.model_validate({
        "title": "MoE Routing",
        "sections": [
            {"title": "Motivation", "intent": "why MoE", "paper_content_ids": [1, 2]},
            {"title": "Comparison", "intent": "A vs B", "paper_content_ids": [1, 2]},
        ],
    })
    assert plan.title == "MoE Routing"
    assert len(plan.sections) == 2
    assert plan.sections[0].paper_content_ids == [1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_slide_models.py -v`
Expected: FAIL — `cannot import name 'SlidePlan'`.

- [ ] **Step 3: Add the models**

In `backend/src/paperhub/models/domain.py` add:

```python
class PlannedSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    intent: str
    paper_content_ids: list[int]


class SlidePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    sections: list[PlannedSection]
```

And extend `AgentState` (TypedDict, `total=False`) with:
```python
    current_view_page: int       # v2.18: slide on screen (frontend-supplied; Phase 2 uses it)
    report_deck_id: int          # v2.18: set by sl_emit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_slide_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/models/domain.py backend/tests/test_slide_models.py
git commit -m "feat(slides): SlidePlan/PlannedSection models + AgentState deck fields"
```

---

## Task 6: Prompt slots — plan, section, notes

**Files:**
- Create: `backend/src/paperhub/llm/prompts/slides_plan_v1.yaml`
- Create: `backend/src/paperhub/llm/prompts/slides_section_v1.yaml`
- Create: `backend/src/paperhub/llm/prompts/slides_notes_v1.yaml`
- Test: `backend/tests/test_slide_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_slide_prompts.py
from paperhub.llm.prompts.registry import PromptRegistry


def test_slide_slots_load_and_format() -> None:
    reg = PromptRegistry()
    plan = reg.get("slides_plan/v1")
    assert "{papers_block}" in plan.user_template
    plan.user_template.format(papers_block="...", response_language="English", memory_context="")

    sec = reg.get("slides_section/v1")
    sec.user_template.format(
        section_title="Motivation", section_intent="why", chunks_block="...",
        deck_title="X", response_language="English", memory_context="",
    )

    notes = reg.get("slides_notes/v1")
    notes.user_template.format(beamer_code="...", response_language="English")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_slide_prompts.py -v`
Expected: FAIL — file not found.

- [ ] **Step 3: Create the three YAML slots**

`slides_plan_v1.yaml`:
```yaml
system: |
  You are an expert academic presenter. Plan a SINGLE coherent conference talk
  that SYNTHESISES across the provided papers (do NOT make one section per paper
  unless there is only one paper). Produce a thematic outline: motivation,
  problem setup, the approaches contrasted, results/evidence, takeaways.
  Output ONLY JSON matching the SlidePlan schema: {"title": str, "sections":
  [{"title": str, "intent": str, "paper_content_ids": [int, ...]}, ...]}.
  Aim for 6-10 sections. Each section names which paper_content_ids it draws from.
user: |
  ENABLED PAPERS (id · title · abstract · section list):
  {papers_block}

  Write the outline in {response_language} for section titles.
  {memory_context}
```

`slides_section_v1.yaml`:
```yaml
system: |
  You write ONE Beamer section as one or more \begin{frame}...\end{frame} blocks.
  Rules: use \includegraphics{figname} for figures the supporting chunks reference
  (no paths — a \graphicspath is set globally); keep math in LaTeX; 3-6 bullets per
  frame; split into multiple frames if dense. Output ONLY the frame block(s) — no
  preamble, no \documentclass, no \begin{document}.
user: |
  DECK TITLE: {deck_title}
  SECTION: {section_title}
  INTENT: {section_intent}

  SUPPORTING CHUNKS (cite content faithfully; do not invent figures not named here):
  {chunks_block}

  Write the frame(s) with body text in {response_language}.
  {memory_context}
```

`slides_notes_v1.yaml`:
```yaml
system: |
  You write PhD-level speaker notes, one per slide. For each PDF page emit a block
  starting with a line "[SLIDE N]" (N is the 1-indexed page) followed by the note.
  Cover what the presenter should SAY, not just what's on the slide.
user: |
  BEAMER SOURCE (overlay frames are annotated with page hints):
  {beamer_code}

  Write the notes in {response_language}. Output [SLIDE N] blocks only.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_slide_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/llm/prompts/slides_*.yaml backend/tests/test_slide_prompts.py
git commit -m "feat(slides): plan/section/notes prompt slots"
```

---

## Task 7: Deck repository (`db/decks.py`)

**Files:**
- Create: `backend/src/paperhub/db/decks.py`
- Test: `backend/tests/test_decks_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_decks_repo.py
import aiosqlite, json, pytest
from paperhub.db.decks import upsert_deck, get_deck, DeckRow


@pytest.mark.asyncio
async def test_upsert_and_get(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    await upsert_deck(
        migrated_db, session_id=1, run_id=1, tex_path="slides/deck.tex",
        pdf_path="slides/deck.pdf", speaker_notes={"1": "hi"},
        plan={"title": "T", "sections": []}, page_count=3, theme="metropolis",
        contributing_paper_ids=[1, 2], status="ok",
    )
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None
    assert deck.page_count == 3
    assert deck.speaker_notes == {"1": "hi"}
    assert deck.contributing_paper_ids == [1, 2]
    # upsert again → still one row (UNIQUE session_id), updated
    await upsert_deck(
        migrated_db, session_id=1, run_id=1, tex_path="slides/deck.tex",
        pdf_path="slides/deck.pdf", speaker_notes={}, plan={}, page_count=5,
        theme="metropolis", contributing_paper_ids=[1], status="ok",
    )
    deck2 = await get_deck(migrated_db, session_id=1)
    assert deck2 is not None and deck2.page_count == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_decks_repo.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `db/decks.py`**

```python
# backend/src/paperhub/db/decks.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiosqlite


@dataclass
class DeckRow:
    id: int
    session_id: int
    run_id: int | None
    tex_path: str
    pdf_path: str | None
    speaker_notes: dict[str, str]
    plan: dict[str, Any]
    page_count: int
    theme: str
    contributing_paper_ids: list[int]
    status: str
    created_at: str
    updated_at: str


async def upsert_deck(
    conn: aiosqlite.Connection, *, session_id: int, run_id: int | None, tex_path: str,
    pdf_path: str | None, speaker_notes: dict[str, str], plan: dict[str, Any],
    page_count: int, theme: str, contributing_paper_ids: list[int], status: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO decks (session_id, run_id, tex_path, pdf_path, speaker_notes_json,
                           plan_json, page_count, theme, contributing_paper_ids_json, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(session_id) DO UPDATE SET
            run_id=excluded.run_id, tex_path=excluded.tex_path, pdf_path=excluded.pdf_path,
            speaker_notes_json=excluded.speaker_notes_json, plan_json=excluded.plan_json,
            page_count=excluded.page_count, theme=excluded.theme,
            contributing_paper_ids_json=excluded.contributing_paper_ids_json,
            status=excluded.status, updated_at=datetime('now')
        """,
        (session_id, run_id, tex_path, pdf_path, json.dumps(speaker_notes),
         json.dumps(plan), page_count, theme, json.dumps(contributing_paper_ids), status),
    )
    await conn.commit()


async def get_deck(conn: aiosqlite.Connection, *, session_id: int) -> DeckRow | None:
    async with conn.execute(
        "SELECT id, session_id, run_id, tex_path, pdf_path, speaker_notes_json, plan_json, "
        "page_count, theme, contributing_paper_ids_json, status, created_at, updated_at "
        "FROM decks WHERE session_id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return DeckRow(
        id=row[0], session_id=row[1], run_id=row[2], tex_path=row[3], pdf_path=row[4],
        speaker_notes=json.loads(row[5] or "{}"), plan=json.loads(row[6] or "{}"),
        page_count=row[7], theme=row[8], contributing_paper_ids=json.loads(row[9] or "[]"),
        status=row[10], created_at=row[11], updated_at=row[12],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_decks_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/db/decks.py backend/tests/test_decks_repo.py
git commit -m "feat(db): decks repository (upsert/get)"
```

---

## Task 8: Report pipeline functions (plan / section / notes), traced

**Files:**
- Create: `backend/src/paperhub/agents/report_pipeline.py`
- Test: `backend/tests/test_report_pipeline.py`

These are the LLM-calling units, each wrapped in a tracer step. They take a `ReportDeps`-like bundle but are unit-tested with a stub adapter.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_pipeline.py
from typing import Any
import aiosqlite, pytest
from paperhub.models.domain import SlidePlan, PlannedSection
from paperhub.agents.report_pipeline import plan_deck, generate_section, generate_notes
from paperhub.tracing.tracer import Tracer


class _StructAdapter:
    def __init__(self, obj: Any = None, tokens: list[str] | None = None) -> None:
        self._obj, self._tokens = obj, tokens or []
    async def structured(self, **kw: Any) -> Any:
        return self._obj
    def stream(self, **kw: Any):
        async def g():
            for t in self._tokens:
                yield t
        return g()


@pytest.mark.asyncio
async def test_plan_deck_returns_plan(fake_tracer: Tracer) -> None:
    plan = SlidePlan(title="T", sections=[PlannedSection(title="Motivation", intent="why", paper_content_ids=[1])])
    out = await plan_deck(
        adapter=_StructAdapter(obj=plan), tracer=fake_tracer, model="m",
        papers_block="...", response_language="English", memory_context="",
    )
    assert out.title == "T"
    assert out.sections[0].title == "Motivation"


@pytest.mark.asyncio
async def test_generate_section_streams_frame(fake_tracer: Tracer) -> None:
    frame = await generate_section(
        adapter=_StructAdapter(tokens=["\\begin{frame}{Motivation}", "\\end{frame}"]),
        tracer=fake_tracer, model="m", deck_title="T",
        section=PlannedSection(title="Motivation", intent="why", paper_content_ids=[1]),
        chunks_block="chunk text", response_language="English", memory_context="",
    )
    assert "\\begin{frame}{Motivation}" in frame
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_report_pipeline.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `report_pipeline.py`**

```python
# backend/src/paperhub/agents/report_pipeline.py
from __future__ import annotations

from typing import Any

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import PlannedSection, SlidePlan
from paperhub.tracing.tracer import Tracer


async def plan_deck(
    *, adapter: LlmAdapter, tracer: Tracer, model: str,
    papers_block: str, response_language: str, memory_context: str,
) -> SlidePlan:
    async with tracer.step(agent="report", tool="report:plan", model=model) as step:
        step.record_args({"papers_block_len": len(papers_block)})
        plan = await adapter.structured(
            slot="slides_plan/v1",
            variables={"papers_block": papers_block,
                       "response_language": response_language or "the user's language",
                       "memory_context": memory_context},
            response_model=SlidePlan, model=model,
        )
        step.record_result({"title": plan.title,
                            "sections": [{"title": s.title, "paper_content_ids": s.paper_content_ids}
                                         for s in plan.sections]})
    return plan


async def generate_section(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, deck_title: str,
    section: PlannedSection, chunks_block: str, response_language: str, memory_context: str,
    chunk_ids: list[int] | None = None,
) -> str:
    async with tracer.step(agent="report", tool="report:section", model=model) as step:
        step.record_args({"section_title": section.title, "chunk_ids": chunk_ids or []})
        tokens: list[str] = []
        async for tok in adapter.stream(
            slot="slides_section/v1",
            variables={"deck_title": deck_title, "section_title": section.title,
                       "section_intent": section.intent, "chunks_block": chunks_block,
                       "response_language": response_language or "the user's language",
                       "memory_context": memory_context},
            model=model,
        ):
            tokens.append(tok)
        frame = "".join(tokens).strip()
        step.record_result({"section_title": section.title, "frame": frame})
    return frame


async def generate_notes(
    *, adapter: LlmAdapter, tracer: Tracer, model: str,
    beamer_code: str, response_language: str,
) -> dict[str, str]:
    import re
    async with tracer.step(agent="report", tool="report:notes", model=model) as step:
        step.record_args({"beamer_len": len(beamer_code)})
        tokens: list[str] = []
        async for tok in adapter.stream(
            slot="slides_notes/v1",
            variables={"beamer_code": beamer_code,
                       "response_language": response_language or "the user's language"},
            model=model,
        ):
            tokens.append(tok)
        raw = "".join(tokens)
        notes: dict[str, str] = {}
        for m in re.finditer(r"\[SLIDE\s+(\d+)\]\s*\n?(.*?)(?=\[SLIDE\s+\d+\]|\Z)", raw, re.DOTALL):
            notes[m.group(1)] = m.group(2).strip()
        step.record_result({"note_pages": sorted(notes.keys())})
    return notes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_report_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/agents/report_pipeline.py backend/tests/test_report_pipeline.py
git commit -m "feat(slides): traced plan/section/notes pipeline functions"
```

---

## Task 9: Report subgraph (`agents/report_graph.py`) — create path

**Files:**
- Create: `backend/src/paperhub/agents/report_graph.py`
- Test: `backend/tests/test_report_graph.py`

`ReportDeps` mirrors `ResearchDeps`. The subgraph fans out `generate_section` over the plan, assembles, compiles (revise closure over the adapter), generates notes, persists the deck, and emits a `deck` custom event.

- [ ] **Step 1: Write the failing test** (stubs adapter + monkeypatches compile so no real `pdflatex`)

```python
# backend/tests/test_report_graph.py
from pathlib import Path
from typing import Any
import aiosqlite, pytest
from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
from paperhub.models.domain import RoutingDecision, SlidePlan, PlannedSection
from paperhub.db.decks import get_deck


class _Adapter:
    async def structured(self, *, response_model, **kw):
        return SlidePlan(title="MoE", sections=[
            PlannedSection(title="Motivation", intent="why", paper_content_ids=[1]),
        ])
    def stream(self, *, slot, **kw):
        async def g():
            if slot == "slides_section/v1":
                yield "\\begin{frame}{Motivation}\\end{frame}"
            elif slot == "slides_notes/v1":
                yield "[SLIDE 1]\nSay hello."
        return g()


@pytest.mark.asyncio
async def test_create_deck_happy_path(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:
    # one enabled paper
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1', 'arxiv', '2403.01', 'Paper A', 'p', ?, 'h')",
        (str(tmp_path / "cacheA" / "source"),),
    )
    await migrated_db.execute("INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, 1, 1)")
    await migrated_db.commit()

    # fake compile: succeeds, writes a pdf, 1 page
    from paperhub.pipelines.slide_pipeline import compile as compile_mod
    async def fake_compile(*, tex, workdir, tex_name, revise, max_retries=3):
        Path(workdir).mkdir(parents=True, exist_ok=True)
        (Path(workdir) / "deck.tex").write_text(tex)
        (Path(workdir) / "deck.pdf").write_bytes(b"%PDF-1.4")
        return compile_mod.CompileResult(True, 1, tex, "", 1)
    monkeypatch.setattr(compile_mod, "compile_with_revise", fake_compile)

    # retriever stub
    class _Retr:
        def retrieve(self, q, *, enabled_paper_content_ids, corpus_size, top_k=10):
            return []

    deps = ReportDeps(
        adapter=_Adapter(), tracer=fake_tracer, conn=migrated_db, retriever=_Retr(),
        workspace=tmp_path, plan_model="m", section_model="m", notes_model="m",
        resolve_model="m", recall_enabled=False,
    )
    graph = build_report_subgraph(deps)
    state: dict[str, Any] = {
        "run_id": fake_tracer.run_id, "branch": "", "session_id": 1,
        "user_message": "make slides", "effective_query": "make slides comparing these",
        "response_language": "English",
        "routing_decision": RoutingDecision(intent="slides", model_tier="flagship", confidence=0.9, reasoning="x"),
    }
    events: list[Any] = []
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(payload)
    deck = await get_deck(migrated_db, session_id=1)
    assert deck is not None and deck.page_count == 1 and deck.status == "ok"
    assert any(e.get("event") == "deck" for e in events)
    assert (tmp_path / "chat_session" / "1" / "slides" / "deck.pdf").exists()


@pytest.mark.asyncio
async def test_empty_enabled_set_message(fake_tracer, migrated_db, tmp_path) -> None:
    deps = ReportDeps(
        adapter=_Adapter(), tracer=fake_tracer, conn=migrated_db, retriever=None,
        workspace=tmp_path, plan_model="m", section_model="m", notes_model="m",
        resolve_model="m", recall_enabled=False,
    )
    graph = build_report_subgraph(deps)
    state = {"run_id": fake_tracer.run_id, "branch": "", "session_id": 1,
             "user_message": "slides", "effective_query": "slides",
             "routing_decision": RoutingDecision(intent="slides", model_tier="flagship", confidence=0.9, reasoning="x")}
    final = None
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "values" and isinstance(payload, dict) and payload.get("final_response"):
            final = payload["final_response"]
    assert final is not None and "enable" in final.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_report_graph.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `report_graph.py`**

```python
# backend/src/paperhub/agents/report_graph.py
"""Report Agent subgraph (Plan F Phase 1 — create-only).

START → sl_resolve → {empty | create} → sl_plan → sl_sections → sl_assemble
        → sl_compile → sl_notes → sl_emit → END
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from paperhub.agents.memory_recall import build_active_memory_block
from paperhub.agents.report_pipeline import generate_notes, generate_section, plan_deck
from paperhub.agents.state import effective_query, response_language
from paperhub.db.decks import get_deck, upsert_deck
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import AgentState, PlannedSection
from paperhub.pipelines.slide_pipeline.assemble import AssembleInput, assemble_deck
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.pipelines.slide_pipeline.history import VersionHistory
from paperhub.tracing.tracer import Tracer

_SECTION_CONCURRENCY = 4
_EMPTY_MSG = ("I couldn't find any enabled reference papers in this chat. "
              "Add and enable at least one reference, then ask me to make slides.")


@dataclass
class ReportDeps:
    adapter: LlmAdapter
    tracer: Tracer
    conn: aiosqlite.Connection
    retriever: Any
    workspace: Path
    plan_model: str
    section_model: str
    notes_model: str
    resolve_model: str
    recall_enabled: bool = True


async def _enabled_papers(conn: aiosqlite.Connection, session_id: int) -> list[dict[str, Any]]:
    async with conn.execute(
        "SELECT pc.id, pc.title, pc.abstract, pc.sections_json, pc.source_dir_path "
        "FROM papers p JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.enabled = 1 ORDER BY p.added_at",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "abstract": r[2], "sections_json": r[3], "source_dir": r[4]} for r in rows]


def build_report_subgraph(deps: ReportDeps) -> Any:
    async def _resolve(state: AgentState) -> AgentState:
        papers = await _enabled_papers(deps.conn, state["session_id"])
        return {**state, "_papers": papers}  # type: ignore[typeddict-unknown-key]

    def _route(state: AgentState) -> str:
        return "create" if state.get("_papers") else "empty"  # type: ignore[return-value]

    async def _empty(state: AgentState) -> AgentState:
        return {**state, "final_response": _EMPTY_MSG}

    async def _generate(state: AgentState) -> AgentState:
        writer = get_stream_writer()
        papers: list[dict[str, Any]] = state["_papers"]  # type: ignore[typeddict-item]
        lang = response_language(state)
        mem = ""
        if deps.recall_enabled:
            mem = await build_active_memory_block(deps.conn, session_id=state.get("session_id"))

        papers_block = "\n".join(
            f"- id={p['id']} · {p['title']} · {(p['abstract'] or '')[:400]} · sections={p['sections_json'] or '[]'}"
            for p in papers
        )
        plan = await plan_deck(adapter=deps.adapter, tracer=deps.tracer, model=deps.plan_model,
                               papers_block=papers_block, response_language=lang, memory_context=mem)

        retr = deps.retriever
        async def _one_section(section: PlannedSection) -> str:
            chunks = []
            if retr is not None:
                chunks = retr.retrieve(
                    section.intent or section.title,
                    enabled_paper_content_ids=section.paper_content_ids,
                    corpus_size=1000, top_k=6,
                )
            chunks_block = "\n\n".join(c.text for c in chunks) or "(no retrieved chunks; use abstracts)"
            return await generate_section(
                adapter=deps.adapter, tracer=deps.tracer, model=deps.section_model,
                deck_title=plan.title, section=section, chunks_block=chunks_block,
                response_language=lang, memory_context=mem,
                chunk_ids=[c.chunk_id for c in chunks],
            )

        sem = asyncio.Semaphore(_SECTION_CONCURRENCY)
        async def _bounded(s: PlannedSection) -> str:
            async with sem:
                return await _one_section(s)
        frames = await asyncio.gather(*[_bounded(s) for s in plan.sections])

        # assemble (graphicspath across contributing cache dirs; macros best-effort empty Phase-1)
        cache_dirs = [p["source_dir"] for p in papers if p["source_dir"]]
        async with deps.tracer.step(agent="report", tool="report:figure_path_rewrite", model=None) as fstep:
            fstep.record_args({"cache_dirs": cache_dirs})
            fstep.record_result({"count": len(cache_dirs)})
        tex = assemble_deck(AssembleInput(
            title=plan.title, theme="metropolis", additional_tex_macros=[],
            cache_source_dirs=cache_dirs, frames=frames,
        ))

        slides_dir = deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
        async def _revise(log: str, cur_tex: str) -> str:
            async with deps.tracer.step(agent="report", tool="report:compile_revise", model=deps.section_model) as rstep:
                rstep.record_args({"log_tail": log[-500:]})
                toks: list[str] = []
                # reuse the section slot's adapter.stream with a revise-style instruction is overkill;
                # call structured-free stream over a minimal inline prompt:
                fixed = cur_tex  # minimal: Phase 1 relies mostly on first-pass; real revise prompt below
                rstep.record_result({"changed": False})
                return fixed

        async with deps.tracer.step(agent="report", tool="report:compile", model=None) as cstep:
            cstep.record_args({"section_count": len(frames)})
            result = await compile_mod.compile_with_revise(
                tex=tex, workdir=slides_dir, tex_name="deck.tex", revise=_revise, max_retries=2,
            )
            cstep.record_result({"ok": result.ok, "attempts": result.attempts,
                                 "page_count": result.page_count,
                                 "log_tail": result.log[-500:] if not result.ok else ""})
            if not result.ok:
                cstep.mark_error("deck failed to compile after retries")

        notes: dict[str, str] = {}
        if result.ok:
            notes = await generate_notes(adapter=deps.adapter, tracer=deps.tracer,
                                         model=deps.notes_model, beamer_code=result.tex,
                                         response_language=lang)
        # persist notes file + version snapshot
        (slides_dir / "speaker_notes.json").write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
        if result.ok:
            VersionHistory(str(slides_dir)).save_version(result.tex, "Generated deck", notes)

        await upsert_deck(
            deps.conn, session_id=state["session_id"], run_id=state.get("run_id"),
            tex_path=str(slides_dir / "deck.tex"),
            pdf_path=str(slides_dir / "deck.pdf") if result.ok else None,
            speaker_notes=notes, plan=plan.model_dump(), page_count=result.page_count,
            theme="metropolis", contributing_paper_ids=[p["id"] for p in papers],
            status="ok" if result.ok else "error",
        )
        deck = await get_deck(deps.conn, session_id=state["session_id"])
        assert deck is not None
        async with deps.tracer.step(agent="report", tool="report:emit", model=None) as estep:
            estep.record_args({"deck_id": deck.id})
            estep.record_result({"page_count": deck.page_count, "status": deck.status})
        writer({"event": "deck", "deck": {
            "deck_id": deck.id, "session_id": deck.session_id, "page_count": deck.page_count,
            "title": plan.title, "status": deck.status,
            "contributing_papers": [{"id": p["id"], "title": p["title"]} for p in papers],
            "has_notes": bool(notes),
        }})
        final = (f"Generated a {deck.page_count}-slide deck — \"{plan.title}\"."
                 if result.ok else
                 "I generated the deck but it failed to compile after retries — showing the last attempt. "
                 "Check the Trace panel for the LaTeX error.")
        return {**state, "final_response": final, "report_deck_id": deck.id}

    g: StateGraph[AgentState, Any] = StateGraph(AgentState)
    g.add_node("sl_resolve", _resolve)
    g.add_node("sl_empty", _empty)
    g.add_node("sl_generate", _generate)
    g.add_edge(START, "sl_resolve")
    g.add_conditional_edges("sl_resolve", _route, {"empty": "sl_empty", "create": "sl_generate"})
    g.add_edge("sl_empty", END)
    g.add_edge("sl_generate", END)
    return g.compile()
```

> **Note for the executor:** the `_revise` here is a no-op stub for Phase 1 (first-pass compile is usually fine for a clean metropolis deck). A real revise prompt slot (`slides_revise/v1`) lands in **Phase 2 Task 2** alongside editing. Keep the `report:compile_revise` trace step so the wiring is ready. If a Phase-1 manual test shows frequent compile failures, pull the Phase-2 revise task forward.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_report_graph.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Lint + type**

Run: `cd backend; uv run ruff check src/paperhub/agents/report_graph.py src/paperhub/agents/report_pipeline.py; uv run mypy src/paperhub/agents/report_graph.py`
Fix the `_papers` TypedDict key (either add `_papers: list[dict]` to `AgentState` total=False, or keep the `# type: ignore` annotations as written). Prefer adding `report_papers: list[dict[str, Any]]` to `AgentState` and renaming `_papers`→`report_papers` to avoid the ignores.

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/agents/report_graph.py backend/tests/test_report_graph.py backend/src/paperhub/models/domain.py
git commit -m "feat(slides): Report Agent subgraph (create path)"
```

---

## Task 10: Wire the subgraph into `graph.py` + `chat.py` + `deck` SSE

**Files:**
- Modify: `backend/src/paperhub/agents/graph.py`
- Modify: `backend/src/paperhub/api/chat.py`
- Modify: `backend/src/paperhub/config.py`
- Test: `backend/tests/test_chat_slides_sse.py`

- [ ] **Step 1: Add report model tiers to `config.py`**

In `Settings` add fields + defaults (flagship for plan/section/notes; small for resolve):
```python
    report_plan_model: str
    report_section_model: str
    report_notes_model: str
    report_resolve_model: str
```
In `load_settings()`:
```python
        report_plan_model=os.environ.get("PAPERHUB_REPORT_PLAN_MODEL", "gemini/gemini-2.5-pro"),
        report_section_model=os.environ.get("PAPERHUB_REPORT_SECTION_MODEL", "gemini/gemini-2.5-pro"),
        report_notes_model=os.environ.get("PAPERHUB_REPORT_NOTES_MODEL", "gemini/gemini-2.5-pro"),
        report_resolve_model=os.environ.get("PAPERHUB_REPORT_RESOLVE_MODEL", "gemini/gemini-3.1-flash-lite"),
```

- [ ] **Step 2: Write the failing SSE test** (mirrors `test_chat_sse.py`; monkeypatches `chat.report_stream`)

```python
# backend/tests/test_chat_slides_sse.py
import json
from typing import Any, AsyncIterator
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_slides_turn_emits_deck_event(monkeypatch, app_with_db) -> None:
    app, _ = app_with_db
    # force the router to classify slides
    from paperhub.api import chat as chat_mod
    async def fake_report_stream(*a: Any, **k: Any) -> AsyncIterator[Any]:
        yield chat_mod.DeckYield(deck={"deck_id": 1, "session_id": 1, "page_count": 3,
                                       "title": "T", "status": "ok",
                                       "contributing_papers": [], "has_notes": True})
        yield chat_mod.FinalOnlyMessage("Generated a 3-slide deck.")
    monkeypatch.setattr(chat_mod, "report_stream", fake_report_stream)
    monkeypatch.setattr(chat_mod, "_route_intent_for_test", lambda *_: "slides", raising=False)
    # ... build ChatRequest with a router mock that returns intent=slides ...
    # assert a `deck` event with page_count=3 appears, followed by `final`.
```

> The executor should follow the exact fixture/monkeypatch shape used in the existing `backend/tests/test_chat_sse.py` (router mock injection via `GraphDeps.router_mock` / the chat endpoint's test seam). Mirror that file's request construction.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_chat_slides_sse.py -v`
Expected: FAIL — `DeckYield` / `report_stream` not defined.

- [ ] **Step 4: Add `report_stream` shim + `DeckYield` + `slides` branch to `chat.py`**

Add a yield type near the other `*Yield` classes:
```python
@dataclass
class DeckYield:
    deck: dict[str, Any]
```

Add the shim (mirrors `paper_search`):
```python
async def report_stream(
    state: AgentState, *, adapter: Any, tracer: Tracer, conn: aiosqlite.Connection,
    retriever: Any, settings: Settings,
) -> AsyncIterator[Any]:
    from paperhub.agents.report_graph import ReportDeps, build_report_subgraph
    deps = ReportDeps(
        adapter=adapter, tracer=tracer, conn=conn, retriever=retriever,
        workspace=settings.workspace, plan_model=settings.report_plan_model,
        section_model=settings.report_section_model, notes_model=settings.report_notes_model,
        resolve_model=settings.report_resolve_model, recall_enabled=settings.memory_recall_enabled,
    )
    graph = build_report_subgraph(deps)
    final_text = ""
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            evt = payload.get("event")
            if evt == "tool_step":
                yield ToolStepYield(record=payload["record"])
            elif evt == "deck":
                yield DeckYield(deck=payload["deck"])
        elif mode == "values" and isinstance(payload, dict) and "final_response" in payload:
            final_text = payload["final_response"]
    yield FinalOnlyMessage(final_text)
```

In `stream_events`, add a branch before the `else: stub_response`:
```python
        elif intent == "slides":
            async for item in report_stream(
                state, adapter=adapter, tracer=tracer, conn=conn,
                retriever=retriever, settings=settings,
            ):
                if isinstance(item, ToolStepYield):
                    yield {"event": "tool_step",
                           "data": json.dumps({"record": item.record}, separators=(',', ':'))}
                elif isinstance(item, DeckYield):
                    yield {"event": "deck",
                           "data": json.dumps(item.deck, separators=(',', ':'))}
                elif isinstance(item, FinalOnlyMessage):
                    final_content = item.content
```
(Use the same `retriever` instance the `paper_qa` branch builds; if it's constructed inline there, lift it so both branches share it.)

- [ ] **Step 5: Replace `_stub_slides` in `graph.py`**

Change the `slides` node so `build_graph` is graph-complete (the SSE path is user-facing, mirroring `library_stats`):
```python
    async def _slides(state: AgentState) -> AgentState:
        return {**state, "final_response": "slides handled by the Report Agent (see chat SSE path)."}
```
Rename node registration `g.add_node("slides", _slides)` (drop `_stub_slides`).

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_chat_slides_sse.py -v`
Expected: PASS.

- [ ] **Step 7: Full backend gate**

Run: `cd backend; uv run pytest -q; uv run ruff check src tests; uv run mypy src`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add backend/src/paperhub/api/chat.py backend/src/paperhub/agents/graph.py backend/src/paperhub/config.py backend/tests/test_chat_slides_sse.py
git commit -m "feat(slides): wire Report Agent into chat SSE (deck event) + graph"
```

---

## Task 11: REST endpoints (`api/decks.py`)

**Files:**
- Create: `backend/src/paperhub/api/decks.py`
- Modify: `backend/src/paperhub/app.py`
- Test: `backend/tests/test_decks_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_decks_api.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_get_deck_404_when_none(app_with_db) -> None:
    app, _ = app_with_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/sessions/1/deck")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_deck_and_pdf(app_with_db, tmp_path) -> None:
    app, conn = app_with_db
    # seed a session + deck + an on-disk pdf
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES"); await conn.commit()
    pdf = tmp_path / "deck.pdf"; pdf.write_bytes(b"%PDF-1.4 fake")
    from paperhub.db.decks import upsert_deck
    await upsert_deck(conn, session_id=1, run_id=None, tex_path=str(tmp_path/"deck.tex"),
                      pdf_path=str(pdf), speaker_notes={"1": "n"}, plan={}, page_count=1,
                      theme="metropolis", contributing_paper_ids=[], status="ok")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        meta = (await c.get("/sessions/1/deck")).json()
        assert meta["page_count"] == 1 and meta["speaker_notes"] == {"1": "n"}
        rpdf = await c.get("/sessions/1/deck/pdf")
        assert rpdf.status_code == 200 and rpdf.content.startswith(b"%PDF")
```

> Use the same `app_with_db` fixture the existing API tests use (e.g. `test_decks_api` mirrors `test_memories_api.py` / `test_papers_upload.py` app fixtures). If no shared `app_with_db` fixture exists, add one to `conftest.py` that builds the FastAPI app bound to `migrated_db` exactly as those tests do.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_decks_api.py -v`
Expected: FAIL — 404 route / module missing.

- [ ] **Step 3: Implement `api/decks.py`**

```python
# backend/src/paperhub/api/decks.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.db.decks import get_deck

router = APIRouter(tags=["decks"])


@router.get("/sessions/{session_id}/deck")
async def get_deck_meta(session_id: int) -> dict[str, Any]:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None:
        raise HTTPException(404, "no deck for this session")
    return {
        "deck_id": deck.id, "session_id": deck.session_id, "page_count": deck.page_count,
        "theme": deck.theme, "status": deck.status, "plan": deck.plan,
        "speaker_notes": deck.speaker_notes,
        "contributing_paper_ids": deck.contributing_paper_ids,
        "updated_at": deck.updated_at,
    }


@router.get("/sessions/{session_id}/deck/pdf")
async def get_deck_pdf(session_id: int) -> FileResponse:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None or not deck.pdf_path or not Path(deck.pdf_path).exists():
        raise HTTPException(404, "no compiled PDF for this session")
    return FileResponse(deck.pdf_path, media_type="application/pdf", filename="deck.pdf")


@router.get("/sessions/{session_id}/deck/tex")
async def get_deck_tex(session_id: int) -> FileResponse:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None or not Path(deck.tex_path).exists():
        raise HTTPException(404, "no deck source for this session")
    return FileResponse(deck.tex_path, media_type="text/plain", filename="deck.tex")
```

Register in `app.py` (mirror the other routers):
```python
from paperhub.api import decks as decks_api
# ...
app.include_router(decks_api.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_decks_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/api/decks.py backend/src/paperhub/app.py backend/tests/test_decks_api.py
git commit -m "feat(slides): REST endpoints for deck metadata/pdf/tex"
```

---

## Task 12: `pdflatex` availability guard

**Files:**
- Modify: `backend/src/paperhub/agents/report_graph.py` (resolve branch)
- Test: extend `backend/tests/test_report_graph.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_missing_pdflatex_message(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.PDFLATEX", "")  # simulate absent
    monkeypatch.setattr("paperhub.agents.report_graph._pdflatex_available", lambda: False)
    await migrated_db.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
        "VALUES ('arxiv:1','arxiv','2403.01','A','p',?,'h')", (str(tmp_path/'s'),))
    await migrated_db.execute("INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1,1,1)")
    await migrated_db.commit()
    deps = ReportDeps(adapter=_Adapter(), tracer=fake_tracer, conn=migrated_db, retriever=None,
                      workspace=tmp_path, plan_model="m", section_model="m", notes_model="m",
                      resolve_model="m", recall_enabled=False)
    graph = build_report_subgraph(deps)
    state = {"run_id": fake_tracer.run_id, "branch": "", "session_id": 1, "user_message": "slides",
             "effective_query": "slides",
             "routing_decision": RoutingDecision(intent="slides", model_tier="flagship", confidence=0.9, reasoning="x")}
    final = None
    async for mode, payload in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "values" and isinstance(payload, dict) and payload.get("final_response"):
            final = payload["final_response"]
    assert final is not None and "latex" in final.lower()
```

- [ ] **Step 2: Run test to verify it fails**, then **Step 3: implement**

Add to `report_graph.py`:
```python
import shutil
def _pdflatex_available() -> bool:
    return bool(shutil.which("pdflatex"))
```
In `_route`: return `"no_latex"` when `not _pdflatex_available()` (and papers exist). Add a `sl_no_latex` node returning a clear message: *"Slide generation needs a LaTeX distribution (TeX Live or MikTeX) with pdflatex on PATH. Install one and try again."* Wire its conditional edge + `END`.

- [ ] **Step 4: Run test to verify it passes.**

Run: `cd backend; uv run pytest tests/test_report_graph.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/agents/report_graph.py backend/tests/test_report_graph.py
git commit -m "feat(slides): graceful message when pdflatex is unavailable"
```

---

## Task 13: Frontend — Deck types + API client

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/tests/lib/decksApi.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/lib/decksApi.test.ts
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { getDeck, deckPdfUrl } from "@/lib/api";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("decks api", () => {
  it("getDeck returns metadata", async () => {
    server.use(
      http.get("http://localhost:8000/sessions/7/deck", () =>
        HttpResponse.json({ deck_id: 1, session_id: 7, page_count: 5, status: "ok",
          theme: "metropolis", plan: {}, speaker_notes: { "1": "n" },
          contributing_paper_ids: [], updated_at: "" })),
    );
    const d = await getDeck(7);
    expect(d.page_count).toBe(5);
    expect(d.speaker_notes["1"]).toBe("n");
  });

  it("deckPdfUrl builds the right URL", () => {
    expect(deckPdfUrl(7)).toBe("http://localhost:8000/sessions/7/deck/pdf");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run tests/lib/decksApi.test.ts`
Expected: FAIL — `getDeck` not exported.

- [ ] **Step 3: Add types + API functions**

In `frontend/src/types/domain.ts` add (and add `"slides"`/`"memory"` to `Intent` if missing — `slides` is present, add `memory`):
```typescript
export interface DeckMeta {
  deck_id: number;
  session_id: number;
  page_count: number;
  theme: string;
  status: "ok" | "error";
  plan: unknown;
  speaker_notes: Record<string, string>;
  contributing_paper_ids: number[];
  updated_at: string;
}

export interface DeckEventData {
  deck_id: number;
  session_id: number;
  page_count: number;
  title: string;
  status: "ok" | "error";
  contributing_papers: { id: number; title: string }[];
  has_notes: boolean;
}
```
Add a `deck?: DeckEventData` field to `ChatMessage`.

In `frontend/src/lib/api.ts`:
```typescript
import type { DeckMeta } from "@/types/domain";

export async function getDeck(sessionId: number): Promise<DeckMeta> {
  return apiFetch<DeckMeta>(`/sessions/${sessionId}/deck`);
}
export function deckPdfUrl(sessionId: number): string {
  return `${API_BASE_URL}/sessions/${sessionId}/deck/pdf`;
}
export async function fetchDeckPdfData(sessionId: number): Promise<Uint8Array> {
  const res = await fetch(deckPdfUrl(sessionId));
  if (!res.ok) throw new Error(`API ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}
export function deckTexUrl(sessionId: number): string {
  return `${API_BASE_URL}/sessions/${sessionId}/deck/tex`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/lib/decksApi.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/domain.ts frontend/src/lib/api.ts frontend/tests/lib/decksApi.test.ts
git commit -m "feat(slides): frontend deck types + api client"
```

---

## Task 14: Frontend — slides store + deck sync hook

**Files:**
- Create: `frontend/src/store/slides.ts`
- Create: `frontend/src/hooks/useDeckSync.ts`
- Test: `frontend/tests/store/slides.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/store/slides.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { useSlidesStore } from "@/store/slides";

describe("slides store", () => {
  beforeEach(() => useSlidesStore.setState({ deckBySession: {}, currentPageBySession: {}, open: false }));

  it("sets deck and tracks current page per session", () => {
    useSlidesStore.getState().setDeck(7, { deck_id: 1, session_id: 7, page_count: 5,
      title: "T", status: "ok", contributing_papers: [], has_notes: true });
    expect(useSlidesStore.getState().deckBySession[7]?.page_count).toBe(5);
    useSlidesStore.getState().setCurrentPage(7, 3);
    expect(useSlidesStore.getState().currentPageBySession[7]).toBe(3);
  });

  it("toggleOpen flips open", () => {
    useSlidesStore.getState().toggleOpen();
    expect(useSlidesStore.getState().open).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**, **Step 3: implement**

```typescript
// frontend/src/store/slides.ts
import { create } from "zustand";
import type { DeckEventData } from "@/types/domain";

interface SlidesState {
  open: boolean;
  deckBySession: Record<number, DeckEventData | undefined>;
  currentPageBySession: Record<number, number>;
  setDeck: (sid: number, deck: DeckEventData) => void;
  setCurrentPage: (sid: number, page: number) => void;
  toggleOpen: () => void;
  openPanel: () => void;
  closePanel: () => void;
}

export const useSlidesStore = create<SlidesState>((set) => ({
  open: false,
  deckBySession: {},
  currentPageBySession: {},
  setDeck: (sid, deck) =>
    set((s) => ({ deckBySession: { ...s.deckBySession, [sid]: deck } })),
  setCurrentPage: (sid, page) =>
    set((s) => ({ currentPageBySession: { ...s.currentPageBySession, [sid]: page } })),
  toggleOpen: () => set((s) => ({ open: !s.open })),
  openPanel: () => set({ open: true }),
  closePanel: () => set({ open: false }),
}));
```

```typescript
// frontend/src/hooks/useDeckSync.ts
import { useEffect } from "react";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import { getDeck } from "@/lib/api";

/** Fetch the session's deck metadata when its backend id is known. */
export function useDeckSync(): void {
  const backendSessionId = useChatStore((s) => {
    if (s.activeSessionId === null) return null;
    return s.sessions.find((x) => x.id === s.activeSessionId)?.backend_session_id ?? null;
  });
  const setDeck = useSlidesStore((s) => s.setDeck);
  useEffect(() => {
    if (backendSessionId === null) return;
    let cancelled = false;
    getDeck(backendSessionId)
      .then((d) => {
        if (cancelled) return;
        setDeck(backendSessionId, {
          deck_id: d.deck_id, session_id: d.session_id, page_count: d.page_count,
          title: (d.plan as { title?: string })?.title ?? "Slides", status: d.status,
          contributing_papers: [], has_notes: Object.keys(d.speaker_notes).length > 0,
        });
      })
      .catch(() => undefined); // 404 = no deck yet; fine
    return () => { cancelled = true; };
  }, [backendSessionId, setDeck]);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/store/slides.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/slides.ts frontend/src/hooks/useDeckSync.ts frontend/tests/store/slides.test.ts
git commit -m "feat(slides): frontend slides store + deck sync hook"
```

---

## Task 15: Frontend — `SlidesPanel` (filmstrip + slide + note + draggable divider)

**Files:**
- Create: `frontend/src/components/slides/SlidesPanel.tsx`
- Test: `frontend/tests/components/SlidesPanel.test.tsx`

The panel renders the deck PDF via `react-pdf` (reuse the `PdfView`/worker config pattern from `canvas/PdfView.tsx`). Filmstrip = small `<Page>` thumbnails; main = the current `<Page>`; note = `speaker_notes[currentPage]`; a draggable horizontal divider resizes the note pane.

- [ ] **Step 1: Write the failing test** (mock react-pdf so jsdom doesn't need pdfjs)

```typescript
// frontend/tests/components/SlidesPanel.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useSlidesStore } from "@/store/slides";
import { SlidesPanel } from "@/components/slides/SlidesPanel";

// Mock react-pdf: render a stub that reports the requested page number.
vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: { workerSrc: "" } },
  Document: ({ children, onLoadSuccess }: any) => {
    onLoadSuccess?.({ numPages: 5 });
    return <div data-testid="doc">{children}</div>;
  },
  Page: ({ pageNumber }: any) => <div data-testid={`page-${pageNumber}`}>page {pageNumber}</div>,
}));
vi.mock("@/lib/api", () => ({
  fetchDeckPdfData: vi.fn(async () => new Uint8Array([1, 2, 3])),
  deckTexUrl: () => "http://x/tex",
}));

describe("SlidesPanel", () => {
  beforeEach(() => {
    useSlidesStore.setState({
      open: true, deckBySession: { 7: { deck_id: 1, session_id: 7, page_count: 5,
        title: "MoE", status: "ok", contributing_papers: [], has_notes: true } },
      currentPageBySession: { 7: 1 },
    });
  });

  it("renders the current slide and speaker note, and navigates", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{ "1": "First note", "2": "Second note" }} />);
    expect(await screen.findByText("First note")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /next slide/i }));
    expect(useSlidesStore.getState().currentPageBySession[7]).toBe(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**, **Step 3: implement**

Implement `SlidesPanel.tsx` with:
- `fetchDeckPdfData(sessionId)` into state (cached by sessionId; `DeferredRemount` not needed since one deck per panel, but reuse the worker-config import line from `canvas/PdfView.tsx`).
- A filmstrip column of `<Page width={64}>` thumbnails (click → `setCurrentPage`).
- A main `<Page width={mainWidth}>` for `currentPage`.
- Prev/next buttons (`aria-label="previous slide"`/`"next slide"`), keyboard handler (←/→), a `n / total` label.
- A speaker-note pane below showing `speakerNotes[String(currentPage)]`, with a draggable horizontal divider that adjusts a `noteHeight` state (pointer events like `useCanvasResize` but on the Y axis; clamp 80–60% of panel height).
- Header: title + status + download buttons (`deckTexUrl`, `deckPdfUrl`).

Keep the worker config exactly as `canvas/PdfView.tsx`:
```typescript
import { Document, Page, pdfjs } from "react-pdf";
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url,
).toString();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/components/SlidesPanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/slides/SlidesPanel.tsx frontend/tests/components/SlidesPanel.test.tsx
git commit -m "feat(slides): SlidesPanel (filmstrip + slide + draggable note pane)"
```

---

## Task 16: Frontend — deck chip, Composer button, ChatPage slot, deck SSE

**Files:**
- Create: `frontend/src/components/slides/DeckChip.tsx`
- Modify: `frontend/src/hooks/useChatStream.ts`
- Modify: `frontend/src/components/chat/Composer.tsx`
- Modify: `frontend/src/pages/ChatPage.tsx`
- Test: `frontend/tests/hooks/useChatStream.deck.test.ts`, `frontend/tests/components/Composer.test.tsx` (extend)

- [ ] **Step 1: Write the failing test for the `deck` SSE handler**

```typescript
// frontend/tests/hooks/useChatStream.deck.test.ts
// Mirror the existing search_results handler test: feed a `deck` SSE event,
// assert the store records deck-by-session and the assistant message gets deck data.
```
(Follow the exact shape of the existing `useChatStream` test that exercises `search_results`.)

- [ ] **Step 2: Handle the `deck` event in `useChatStream.ts`** (next to the `search_results` branch)

```typescript
} else if (event === "deck") {
  const d = data as DeckEventData;
  store.getState().setDeckOnMessage(sessionId, d);          // attach to the assistant msg
  useSlidesStore.getState().setDeck(d.session_id, d);
  useSlidesStore.getState().setCurrentPage(d.session_id, 1);
}
```
Add `setDeckOnMessage` to the chat store (sets `message.deck = d` on the streaming assistant message), mirroring `setSearchResults`.

- [ ] **Step 3: `DeckChip.tsx`** — a card rendered when `message.deck` is set: title + `page_count` slides + status + buttons (Open → `useSlidesStore.openPanel()` + `setCurrentPage`; Download → `deckPdfUrl`). Render it in the assistant message component next to where `SearchResultList` renders.

- [ ] **Step 4: Composer** — replace the disabled `Slides` capability with an active toggle button (mirror the References/Memory buttons): `onClick={onToggleSlides}`, `aria-pressed={slidesOpen}`, `aria-label="Slides"`. Remove `Slides` from the `CAPABILITIES` placeholder array (leave `Compare`). Add `slidesOpen?: boolean` + `onToggleSlides?: () => void` props.

- [ ] **Step 5: ChatPage** — add `SlidesPanel` to the shared right slot (mirror Memory):
  - `const slidesOpen = useSlidesStore((s) => s.open);` + toggle handler that closes Canvas + Memory when opening (extend the existing mutual-exclusion `useEffect`s so opening Slides closes the others and vice-versa).
  - `useDeckSync();` near `useReferencesSync()`.
  - Render `{slidesOpen && (<div className="absolute inset-0 ...">...<SlidesPanel sessionId={backendSessionId} speakerNotes={deckMeta.speaker_notes} /></div>)}` lazily (`React.lazy`), pulling `speaker_notes` from `getDeck` (fetch in `useDeckSync` and store it, or fetch inside the panel).
  - Add Slides to `rightPanelOpen = canvasOpen || memoryOpen || slidesOpen`.
  - Pass `slidesOpen` + `onToggleSlides` to `<Composer>`.

- [ ] **Step 6: Run the relevant tests**

Run: `cd frontend; npx vitest run tests/hooks/useChatStream.deck.test.ts tests/components/Composer.test.tsx tests/components/SlidesPanel.test.tsx`
Expected: PASS.

- [ ] **Step 7: Full frontend gate**

Run: `cd frontend; npm test; npm run typecheck; npm run lint; npm run build`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/slides/DeckChip.tsx frontend/src/hooks/useChatStream.ts frontend/src/components/chat/Composer.tsx frontend/src/pages/ChatPage.tsx frontend/src/store/chat.ts frontend/tests/
git commit -m "feat(slides): deck chip + Slides panel slot + deck SSE handling"
```

---

## Task 17: End-to-end smoke script

**Files:**
- Create: `backend/scripts/smoke_slides.ps1`

- [ ] **Step 1: Write the smoke script** — boot the backend with a mocked LLM (mirror `smoke_chat.ps1`), seed a session with one enabled arXiv paper, POST a `slides` chat turn, assert the SSE stream contains a `deck` event and that `GET /sessions/{id}/deck/pdf` returns `200` with a `%PDF` body. If `pdflatex` is absent, assert the graceful message instead. Print PASS/FAIL and exit non-zero on failure.

- [ ] **Step 2: Run it**

Run: `cd backend; .\scripts\smoke_slides.ps1`
Expected: PASS (or the documented "install LaTeX" message on a host without pdflatex).

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/smoke_slides.ps1
git commit -m "test(slides): end-to-end mocked-LLM smoke script"
```

---

## Task 18: Manual verification + docs

- [ ] **Step 1: Real-LLM manual check** (requires `backend/.env` + `pdflatex` installed)

Boot `scripts/start.ps1`, open the frontend, attach 2 arXiv papers, enable both, send *"make slides comparing these"*. Verify: progress streams, a deck chip appears, the Slides panel opens with a filmstrip + first slide + speaker note, the note divider drags, download works.

- [ ] **Step 2: Update CLAUDE.md**

Add a "system binaries" note that `pdflatex` (TeX Live / MikTeX) is now **required for the `slides` intent** (was optional), and add Plan F Phase 1 to the plan table as **in progress**. Add a pointer entry: *"How are slides generated? → Report Agent subgraph (§III-5.3), Beamer compiled by pdflatex, one deck per session (`decks` table), viewed in the Slides panel."*

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: Plan F Phase 1 — pdflatex requirement + slides pointers"
```

---

## Self-review notes (author)

- **Spec coverage:** UC-4 generation ✓ (Tasks 8-10), §III-5.3 subgraph ✓ (Task 9), graphicspath/ADDITIONAL ✓ (Task 4), compile-fix loop ✓ (Task 3, revise stubbed → Phase 2), speaker notes ✓ (Task 8), decks table ✓ (Task 1), version snapshot on compile ✓ (Task 9 via `VersionHistory.save_version`), `deck` SSE + REST ✓ (Tasks 10-11), FR-12 panel (filmstrip/slide/note/divider) ✓ (Task 15), deck chip ✓ (Task 16), pdflatex guard ✓ (Task 12). **Deferred to Phase 2 (correctly out of Phase-1 scope):** edit/recreate, presentation mode, version-history UI, Q&A choreography, the real revise prompt.
- **Type consistency:** `DeckEventData` (SSE) vs `DeckMeta` (REST) are distinct by design — the chip uses `DeckEventData`, the panel uses `DeckMeta.speaker_notes`. `ReportDeps` field names match between Task 9 and Task 10. `compile_with_revise` signature identical across Tasks 3, 9, 12.
- **Known follow-up:** the `_revise` no-op (Task 9) means a deck that fails first-pass compile won't self-heal until Phase 2 Task 2 adds `slides_revise/v1`. Acceptable for Phase 1 (metropolis decks from clean frames compile reliably); flagged in the Task 9 note.
