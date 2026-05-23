# Plan F3 — PhD-grade slide agent (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace F1's `plan → blind sections → no-op revise → notes` with a multi-pass LangGraph agent that simulates how a PhD builds a conference talk, consuming F2's `PaperAsset` — producing concise slides + rich speaker notes, **never** a non-existent figure, with a self-correcting compile loop.

**Architecture:** `agents/report_graph.py` becomes `sl_resolve → sl_understand (per-paper) → sl_narrate → sl_draft (per-slide frame⟂note pairs) → sl_coherence → sl_assemble (stage assigned figures by deck-unique key) → sl_verify_figures (deterministic) → sl_compile (Overfull-aware revise loop) → sl_notes_finalize → sl_emit`. Figures are assigned in `sl_narrate` ONLY from the `PaperAsset` figure inventory; `sl_verify_figures` deterministically rejects any `\includegraphics` not in the staged set. Cost is no object — flagship throughout.

**Tech Stack:** Python 3.11 + `uv`, LangGraph, LiteLLM adapter, `pdflatex` (Beamer), Pydantic, `PaperAsset` (F2).

**Spec:** SRS v2.19 — §III-5.3 (PhD slide-agent topology + three hard contracts), §III-3 Report Agent row. **Depends on F2** (`PaperAsset` + `read_paper_asset`). F4 (presentation/editing) builds on this.

**Conventions:** TDD; backend gates `uv run pytest|ruff|mypy`; Conventional Commits + `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`. Tests stub the adapter + monkeypatch `compile_with_revise` (no real LLM/pdflatex).

**Reused from F1 (NOT nuked):** `decks` table + `db/decks.py`, deck REST (`api/decks.py`), the Slides panel + deck chip + filmstrip resize, `compile.py:compile_with_revise` (extended here), `assemble.py:assemble_deck`/`build_graphicspath`, `history.py`, `report_stream` shim in `chat.py`, the `deck` SSE event. **Nuked/rewritten:** `slides_plan_v1.yaml`/`slides_section_v1.yaml`/`slides_notes_v1.yaml`, `pipelines/slide_pipeline/figures.py` (flat-stem approach), the `_revise` no-op, the F1 `plan_deck`/`generate_section`/`generate_notes` in `report_pipeline.py`.

---

## File Structure

**New:**
- `backend/src/paperhub/pipelines/slide_pipeline/figure_inventory.py` — read each enabled paper's `PaperAsset`, build a deck-wide figure inventory (`InventoryFigure{key, caption, abs_path, paper_id}`, collision-free keys), `stage_inventory(figs, dest_dir)`, `verify_and_fix_graphics(tex, allowed_keys) -> (tex, list[str] rejected)`.
- Prompts: `slides_understand_v1.yaml`, `slides_narrate_v1.yaml`, `slides_draft_v1.yaml`, `slides_coherence_v1.yaml`, `slides_revise_v1.yaml`.

**Modified:**
- `models/domain.py` — `PaperBrief`, `OutlineSlide`, `TalkOutline`, `SlideDraft`.
- `agents/report_pipeline.py` — replace F1 fns with `understand_paper`, `narrate_talk`, `draft_slide`, `coherence_pass`, `revise_frame`, `finalize_notes` (all traced).
- `agents/report_graph.py` — the new topology.
- `pipelines/slide_pipeline/compile.py` — Overfull-vbox detection → revise trigger.
- `config.py` — model tiers (reuse `report_*`; add `report_understand_model`, `report_draft_model`, `report_coherence_model` defaulting to flagship).

**Deleted:** `slides_plan_v1.yaml`, `slides_section_v1.yaml` (kept: a rewritten `slides_notes` is replaced by `slides_draft` producing notes inline + `slides_notes_finalize` reconcile). The old `figures.py` is replaced by `figure_inventory.py` (delete `figures.py` + its tests, or repoint).

**Tests:** `test_figure_inventory.py`, `test_report_pipeline.py` (rewritten), `test_report_graph.py` (rewritten), `test_slide_compile.py` (Overfull case), `test_slide_prompts.py` (new slots).

---

## Task 1: Figure inventory — collision-free staging + deterministic verify

**Files:**
- Create: `backend/src/paperhub/pipelines/slide_pipeline/figure_inventory.py`
- Test: `backend/tests/test_figure_inventory.py`

- [ ] **Step 1: Write the failing test:**

```python
# backend/tests/test_figure_inventory.py
from pathlib import Path
from paperhub.pipelines.paper_asset import PaperAsset, FigureAsset, write_paper_asset, paper_asset_dir
from paperhub.pipelines.slide_pipeline.figure_inventory import (
    build_inventory, stage_inventory, verify_and_fix_graphics, InventoryFigure,
)


def _paper(tmp: Path, pid: int, stem: str) -> dict:
    d = tmp / f"p{pid}"
    fa = paper_asset_dir(d) / "figures"; fa.mkdir(parents=True)
    (fa / f"{stem}.png").write_bytes(b"\x89PNG")
    write_paper_asset(PaperAsset(figures=[FigureAsset(id=stem, caption=f"cap {stem}", page=1, section="M", image_path=f"figures/{stem}.png")]), d)
    return {"id": pid, "source_dir": str(d)}


def test_collision_free_keys_and_staging(tmp_path: Path) -> None:
    # two papers BOTH have a figure stem "fig-000" → must not collide
    papers = [_paper(tmp_path, 1, "fig-000"), _paper(tmp_path, 2, "fig-000")]
    inv = build_inventory(papers)
    keys = [f.key for f in inv]
    assert len(set(keys)) == 2  # unique
    assert all(k.startswith("p") for k in keys)
    dest = tmp_path / "deck" / "figures"
    stage_inventory(inv, dest)
    for f in inv:
        assert (dest / f"{f.key}.png").exists()


def test_verify_rejects_unknown_graphics(tmp_path: Path) -> None:
    allowed = {"p0-fig-000"}
    tex = (r"\includegraphics[width=.8\textwidth]{p0-fig-000}" "\n"
           r"\includegraphics{ghost-figure}")
    fixed, rejected = verify_and_fix_graphics(tex, allowed)
    assert "p0-fig-000" in fixed
    assert "ghost-figure" not in fixed                # the hallucinated one is gone
    assert "ghost-figure" in rejected
    assert "[figure omitted" in fixed                 # replaced with a visible placeholder
```

- [ ] **Step 2: Run → FAIL. Step 3: implement `figure_inventory.py`:**

```python
# backend/src/paperhub/pipelines/slide_pipeline/figure_inventory.py
"""Deck figure inventory: collision-free staging + deterministic verification.

Builds a deck-wide inventory from each enabled paper's PaperAsset (F2), staging
the real figure files under deck-unique keys into ONE dir reachable by a single
\\graphicspath. verify_and_fix_graphics is the HARD no-hallucination guarantee:
any \\includegraphics whose key is not a staged inventory key is replaced by a
text placeholder before compile (SRS v2.19 §III-5.3 contract 2).
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from paperhub.pipelines.paper_asset import read_paper_asset

_INCLUDE = re.compile(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}")


@dataclass(frozen=True)
class InventoryFigure:
    key: str          # deck-unique, e.g. "p1-fig-000"
    caption: str
    abs_path: str
    paper_id: int


def build_inventory(papers: list[dict]) -> list[InventoryFigure]:
    """papers: list of {"id": int, "source_dir": str}. Returns deck-unique figs."""
    out: list[InventoryFigure] = []
    for idx, p in enumerate(papers):
        asset = read_paper_asset(Path(p["source_dir"]))
        if asset is None:
            continue
        for f in asset.figures:
            key = f"p{idx}-{f.id}"
            out.append(InventoryFigure(
                key=key, caption=f.caption,
                abs_path=str(f.abs_image_path(Path(p["source_dir"]))),
                paper_id=int(p["id"])))
    return out


def stage_inventory(inv: list[InventoryFigure], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in inv:
        src = Path(f.abs_path)
        if src.exists():
            shutil.copy2(src, dest_dir / f"{f.key}{src.suffix or '.png'}")


def verify_and_fix_graphics(tex: str, allowed_keys: set[str]) -> tuple[str, list[str]]:
    rejected: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        from pathlib import Path as _P
        name = m.group(2).strip()
        stem = _P(name).stem
        if stem in allowed_keys or name in allowed_keys:
            return m.group(0)
        rejected.append(name)
        return r"\textit{[figure omitted]}"

    return _INCLUDE.sub(_repl, tex), rejected
```

- [ ] **Step 4: Run → PASS. Step 5: ruff + mypy. Step 6: Commit** `feat(slides): collision-free figure inventory + deterministic verify`.

---

## Task 2: Domain models for the PhD flow

**Files:**
- Modify: `backend/src/paperhub/models/domain.py`
- Test: `backend/tests/test_slide_models.py` (extend)

- [ ] **Step 1: Write the failing test** parsing each model:

```python
def test_phd_models_parse() -> None:
    from paperhub.models.domain import PaperBrief, OutlineSlide, TalkOutline, SlideDraft
    brief = PaperBrief.model_validate({"paper_id": 1, "contribution": "x", "method": "y",
        "key_results": ["r1"], "key_figure_keys": ["p0-fig-000"], "key_equations": ["E=mc^2"]})
    assert brief.key_figure_keys == ["p0-fig-000"]
    outline = TalkOutline.model_validate({"title": "T", "slides": [
        {"title": "Motivation", "goal": "why", "key_points": ["a", "b"],
         "figure_key": "p0-fig-000", "equation": None, "chunk_ids": [3], "paper_ids": [1]}]})
    assert outline.slides[0].figure_key == "p0-fig-000"
    draft = SlideDraft.model_validate({"frame": r"\begin{frame}{X}\end{frame}", "note": "say this"})
    assert "frame" in draft.frame
```

- [ ] **Step 2: Run → FAIL. Step 3: add to `domain.py`:**

```python
class PaperBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: int
    contribution: str
    method: str
    key_results: list[str]
    key_figure_keys: list[str]   # inventory keys this paper's slides may use
    key_equations: list[str]     # LaTeX

class OutlineSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    goal: str
    key_points: list[str]
    figure_key: str | None = None   # MUST be an inventory key or None
    equation: str | None = None     # LaTeX or None
    chunk_ids: list[int] = []
    paper_ids: list[int] = []

class TalkOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    slides: list[OutlineSlide]

class SlideDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame: str   # the \begin{frame}...\end{frame} block
    note: str    # the rich speaker note for this slide
```

- [ ] **Step 4: Run → PASS. Step 5: ruff + mypy. Step 6: Commit** `feat(slides): PhD-flow domain models`.

---

## Task 3: Prompt slots (understand / narrate / draft / coherence / revise)

**Files:**
- Create: `slides_understand_v1.yaml`, `slides_narrate_v1.yaml`, `slides_draft_v1.yaml`, `slides_coherence_v1.yaml`, `slides_revise_v1.yaml`
- Delete: `slides_plan_v1.yaml`, `slides_section_v1.yaml`, `slides_notes_v1.yaml`
- Test: `backend/tests/test_slide_prompts.py` (rewrite)

- [ ] **Step 1: Write the failing test** that loads + `.format()`s each new slot with its placeholders. Placeholders:
  - `slides_understand/v1` user: `{paper_block}` (title+abstract+sections+figure inventory `key: caption`+equations), `{response_language}`.
  - `slides_narrate/v1` user: `{briefs_block}`, `{figure_inventory}` (all `key: caption` lines), `{response_language}`, `{memory_context}`.
  - `slides_draft/v1` user: `{deck_title}`, `{slide_goal}`, `{slide_title}`, `{key_points}`, `{assigned_figure}` (key+caption or "none"), `{assigned_equation}` (LaTeX or "none"), `{chunks_block}`, `{response_language}`, `{memory_context}`.
  - `slides_coherence/v1` user: `{frames_block}` (all frames), `{response_language}`.
  - `slides_revise/v1` user: `{pdflatex_log}`, `{tex}`.

- [ ] **Step 2: Run → FAIL. Step 3: author the YAMLs.** System blocks enforce the three contracts (cite the SRS quality bar):
  - **understand**: "Read deeply. Output JSON PaperBrief: contribution, method, key_results, key_figure_keys (ONLY from the provided inventory keys), key_equations (LaTeX). Do not invent figure keys."
  - **narrate**: "Design ONE coherent conference talk (synthesis across papers; a single paper → a clean summary). Output JSON TalkOutline. Per slide: a clear goal, ≤4 key points, AT MOST one `figure_key` (ONLY an inventory key) OR one `equation` (LaTeX). Assign figures only where they truly help. Source chunk_ids + paper_ids per slide."
  - **draft**: "Produce a CONCISE Beamer frame AND a RICH speaker note as JSON SlideDraft. Frame rules: ≤4 \\item, each ≤12 words (key point, not a sentence); if an assigned figure: `\\includegraphics[width=0.85\\textwidth,height=0.7\\textheight,keepaspectratio]{KEY}` on its own; if an assigned equation: display it; NO \\pause/overlays; one \\frametitle. The DETAIL goes in the note (what the presenter SAYS — full explanation, grounded in the chunks), not on the slide."
  - **coherence**: "Review ALL frames as one deck. Remove redundancy, fix flow/transitions, enforce consistent style + the density rules. Return the full corrected list of frames (same count or fewer; you may split an overfull frame). Output the frames concatenated."
  - **revise**: "Fix a Beamer doc that failed to compile OR has Overfull-vbox (content off the slide). Given the pdflatex log + source, output the COMPLETE corrected LaTeX — split/tighten overflowing frames, fix errors. No commentary, no fences."

- [ ] **Step 4: Run → PASS. Step 5: delete the 3 old slots** (`git rm slides_plan_v1.yaml slides_section_v1.yaml slides_notes_v1.yaml`). **Step 6: Commit** `feat(slides): PhD-flow prompt slots (nuke plan/section/notes)`.

---

## Task 4: report_pipeline functions (traced)

**Files:**
- Modify: `backend/src/paperhub/agents/report_pipeline.py` (replace F1 fns)
- Test: `backend/tests/test_report_pipeline.py` (rewrite)

- [ ] **Step 1: Write failing tests** with a stub adapter (mirror the F1 `_StructAdapter`) for: `understand_paper` → `PaperBrief`; `narrate_talk` → `TalkOutline`; `draft_slide` → `SlideDraft`; `coherence_pass(frames) -> list[str]`; `revise_frame`/`revise_tex(log, tex) -> str`; `finalize_notes(drafts, page_count) -> dict[str,str]` (pads to page_count).

- [ ] **Step 2: Run → FAIL. Step 3: implement** each as an `async` fn wrapped in `tracer.step(agent="report", tool="report:<stage>", model=...)` recording reconstruct-able state (brief fields, outline, frame+note, rejected figure keys, etc.) per the observability policy. Use `adapter.structured(slot=..., response_model=...)` for understand/narrate/draft; `adapter.stream(...)` joined for coherence/revise. `finalize_notes` deterministically maps drafted notes to `1..page_count`, padding gaps with a short fallback.

- [ ] **Step 4: Run → PASS. Step 5: ruff + mypy. Step 6: Commit** `feat(slides): traced PhD pipeline functions`.

---

## Task 5: Overfull-aware compile loop

**Files:**
- Modify: `backend/src/paperhub/pipelines/slide_pipeline/compile.py`
- Test: `backend/tests/test_slide_compile.py` (extend)

- [ ] **Step 1: Write the failing test** — a fake `_run_pdflatex` that returns rc=0 + a PDF but whose **log contains `Overfull \vbox`** must still trigger one `revise` call (i.e. `compile_with_revise` does not accept an Overfull-vbox run as success on the first attempt; after revise returns a clean log, it succeeds):

```python
@pytest.mark.asyncio
async def test_overfull_vbox_triggers_revise(tmp_path, monkeypatch) -> None:
    workdir = tmp_path / "slides"; workdir.mkdir()
    calls = {"n": 0}
    def fake_run(cmd, cwd=None, **kw):
        import subprocess
        calls["n"] += 1
        Path(cwd, "deck.pdf").write_bytes(b"%PDF")
        log = "Overfull \\vbox (12pt too high)" if calls["n"] == 1 else "ok"
        return subprocess.CompletedProcess(cmd, 0, log, "")
    monkeypatch.setattr("paperhub.pipelines.slide_pipeline.compile.subprocess.run", fake_run)
    revised = {"n": 0}
    async def revise(log, tex):
        revised["n"] += 1
        return tex + "\n% tightened"
    res = await compile_with_revise(tex="...", workdir=workdir, tex_name="deck.tex", revise=revise, max_retries=2)
    assert res.ok is True
    assert revised["n"] == 1  # the Overfull run was revised once, then clean
```

- [ ] **Step 2: Run → FAIL. Step 3:** add `_has_overfull_vbox(log) -> bool` (regex `Overfull \\vbox`); in `compile_with_revise`, success now requires `(returncode == 0 or pdf exists) AND not _has_overfull_vbox(last_log)`. On a clean-exit-but-overfull run, if attempts remain, call `revise(log, tex)` and recompile; otherwise return the last result with `ok` reflecting at least a produced PDF (a deck that can't shed overflow still emits, status downgraded). Keep all blocking calls in `asyncio.to_thread` (already done).

- [ ] **Step 4: Run → PASS** (+ the existing compile tests stay green). **Step 5: ruff + mypy. Step 6: Commit** `feat(slides): treat Overfull-vbox as a fixable compile failure`.

---

## Task 6: report_graph.py — the PhD topology

**Files:**
- Rewrite: `backend/src/paperhub/agents/report_graph.py`
- Test: `backend/tests/test_report_graph.py` (rewrite)

- [ ] **Step 1: Write the failing happy-path test** (stub adapter returns a PaperBrief, a 2-slide TalkOutline, SlideDrafts; monkeypatch `compile_with_revise` to write a PDF + return ok/page_count; seed 1 enabled paper WITH a `PaperAsset` on disk via `write_paper_asset`). Assert: a `deck` event fires; `get_deck` row `status=ok`, `page_count>0`; the staged `slides/figures/` contains the assigned figure; **a frame referencing a non-inventory key would have been neutralized** (add a second test: a draft emitting `\includegraphics{ghost}` → after the graph, the compiled tex contains `[figure omitted]`, not `{ghost}`).

- [ ] **Step 2: Run → FAIL. Step 3: rewrite `build_report_subgraph`** with nodes:
  - `sl_resolve` — enabled papers (each dict gets `source_dir` from `paper_content.source_dir_path`); empty/no-latex guards (keep from F1).
  - `sl_understand` — `asyncio.gather(understand_paper(...) for paper)` building per-paper briefs; pass each paper's `paper_block` (title+abstract+sections from `PaperAsset` + that paper's figure inventory `key: caption` + equations).
  - `sl_narrate` — `narrate_talk(briefs, figure_inventory=all keys)` → `TalkOutline`. **Build the deck-wide inventory** here via `build_inventory(papers)`; pass `key: caption` lines; the outline's `figure_key`s are validated against inventory keys (drop unknowns defensively).
  - `sl_draft` — `asyncio.gather(draft_slide(slide, ...) for slide in outline.slides)`; each gets its assigned figure (key+caption) / equation + retrieved chunks. Collect `(frame, note)` pairs.
  - `sl_coherence` — `coherence_pass(frames)` → corrected frames (re-split list).
  - `sl_assemble` — `stage_inventory(used_figs, slides_dir/"figures")` (only figures actually referenced), `\graphicspath{ {.../figures/} }` (forward-slashed via `build_graphicspath`), merged ADDITIONAL.tex macros (from arXiv papers), `assemble_deck(...)`.
  - `sl_verify_figures` — `tex, rejected = verify_and_fix_graphics(tex, allowed_keys)`; record `rejected` in a trace step; (optional: if `rejected`, re-draft those frames — for v1, neutralize is the hard guarantee; record it).
  - `sl_compile` — `compile_with_revise(..., revise=_revise)` where `_revise` calls `revise_frame`/`revise_tex` (real, Overfull-aware loop from Task 5). Always record the trimmed log (incl. Overfull lines) + attempts + page_count.
  - `sl_notes_finalize` — `finalize_notes(drafts, page_count)` → `{page: note}` padded to page count.
  - `sl_emit` — write `decks`, snapshot version, emit `deck` event (unchanged shape).
  - Tracing tool names per SRS §III-5.3 (`report:understand[paper]`, `report:narrate`, `report:draft[slide]`, `report:coherence`, `report:figure_stage`, `report:verify_figures`, `report:compile`, `report:notes_finalize`, `report:emit`).

- [ ] **Step 4: Run → PASS. Step 5: full gate** `uv run pytest -q; uv run ruff check src tests; uv run mypy src`. **Step 6: Commit** `feat(slides): PhD-grade Report Agent topology`.

---

## Task 7: chat.py wiring sanity + delete dead F1 code

**Files:**
- Modify: `backend/src/paperhub/api/chat.py` (the `report_stream` shim — confirm it still forwards `tool_step` + `deck` + final; no structural change expected).
- Delete: `backend/src/paperhub/pipelines/slide_pipeline/figures.py` + `tests/test_slide_figures.py` (superseded by `figure_inventory.py`); remove F1 `plan_deck`/`generate_section`/`generate_notes` if not already replaced in Task 4.
- Test: `backend/tests/test_chat_slides_sse.py` stays green (the deck SSE contract is unchanged).

- [ ] **Step 1:** Run `uv run pytest tests/test_chat_slides_sse.py -v` — confirm green (the redesign keeps the `deck` event shape). Fix the shim only if a signature changed.
- [ ] **Step 2:** `git rm` the dead `figures.py` + its test (and any `assemble.py` references to the old `collect_figures`/`enforce_graphics_options` — repoint to `figure_inventory`). Run the full gate.
- [ ] **Step 3: Commit** `chore(slides): remove superseded F1 figure code`.

---

## Task 8: Real-API verification + smoke + docs

- [ ] **Step 1: Smoke** — extend `backend/scripts/smoke_slides.ps1` to assert (real LLM + the `marker` service up for a PDF paper, or arXiv): a `deck` event `status=ok`, the deck.tex has NO `\includegraphics` referencing a non-staged key (grep the staged `figures/` keys vs the tex), notes count == page_count.
- [ ] **Step 2: Real-API check** (mirror the F2 verification): boot backend + `marker` (compose) + real LLM; ingest 2 papers (one PDF-only so Marker runs); generate slides; **manually inspect** the deck PDF for: real figures embedded (not omitted placeholders), concise frames (no overflow), one rich note per slide. Capture the `report:verify_figures` trace (should show 0 rejects if narration grounded well). This is the quality gate that F1 failed.
- [ ] **Step 3: Docs** — update CLAUDE.md "How are slides generated?" to the F3 flow; mark F2+F3 status. **Commit** `docs: F3 PhD slide agent`.

---

## Self-review notes (author)
- **Spec coverage (SRS v2.19 §III-5.3):** understand/narrate/draft(pair)/coherence/verify/compile-loop/notes-finalize all have tasks (T4 fns, T6 graph); figure inventory + deterministic verify ✓ (T1); Overfull-aware compile ✓ (T5); three contracts — concise/rich (T3 draft prompt), no-hallucination (T1 verify + T6 sl_verify_figures), self-correcting (T5+T6) ✓.
- **Depends on F2:** `read_paper_asset` + `PaperAsset` figure inventory must exist (F2 T1/T3/T4). Do NOT start F3 until F2's real-fixture reality check (F2 T7 Step 5) passes — otherwise the inventory is empty and every slide degrades to no-figure.
- **Type consistency:** `InventoryFigure.key` ↔ `OutlineSlide.figure_key` ↔ `verify_and_fix_graphics(allowed_keys)` ↔ the staged filename stem all use the same deck-unique key. `SlideDraft.{frame,note}` flows draft→coherence→assemble→notes_finalize.
- **Reused, not rebuilt:** decks/REST/panel/deck-event/compile threading. Frame-edit (`beamer_helpers.replace_frame_in_beamer`) is F4's concern, not F3.
- **Known follow-up:** `sl_verify_figures` neutralizes unknown keys (hard guarantee) but v1 does not re-draft the frame; if real runs show frequent neutralizations, add a bounded re-draft loop (note in T6). The narrate prompt grounding should make rejects rare.
