# Plan F4 — Presentation + Editing + Q&A choreography (Implementation Plan)

> **Renumbered (SRS v2.19):** this was "Plan F Phase 2". The quality redesign inserted **F2 (Marker ingestion)** and **F3 (PhD-grade slide agent)** ahead of it, so presentation+editing is now **F4** and depends on F3's rebuilt `report_graph.py` (the node names below — `sl_edit_plan`, `sl_edit_frame` — will attach to the F3 topology, not the nuked F1 one).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the session deck editable (LLM decides page-vs-deck scope; diff edits; recreate), revertible (version-history UI), and presentable (separate fullscreen audience window synced via `BroadcastChannel`), with the present → answer-an-audience-question → resume loop.

**Architecture:** Extends the Phase-1 `report_graph.py` with an LLM `sl_resolve` classifier (`create`/`recreate`/`edit`, edit→`page:N`/`deck` from `current_view_page`) and edit nodes (`sl_edit_plan` diff + `sl_edit_frame` single, reusing `beamer_helpers`). Adds a real `slides_revise/v1` prompt wired into the compile loop. Version history gets REST + a UI panel. Presentation mode adds a dedicated `present.html` Vite entry (the SPA has no router) driven by `BroadcastChannel`; the Slides panel becomes a presenter view (timer + next-preview). Q&A-during-talk is pure right-slot + persisted-page choreography — no new backend.

**Tech Stack:** as Phase 1, plus a second Vite HTML entry (`present.html`) and the `BroadcastChannel` Web API.

**Depends on:** Phase 1 (this builds directly on `report_graph.py`, `db/decks.py`, `slides` store, `SlidesPanel`, deck SSE/REST).

**Spec:** SRS v2.18 — UC-4 (edit + presentation + Q&A loop), FR-12 (presentation, version history), §III-3 (edit nodes), §III-5.3 (edit nodes, revise loop, version history REST).

**Conventions:** identical to Phase 1 (TDD; backend `uv run pytest|ruff|mypy`; frontend `npm test|typecheck|lint|build`; Conventional Commits; provenance headers on copied code; `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`).

---

## File Structure

**Backend — new:**
- `backend/src/paperhub/llm/prompts/slides_resolve_v1.yaml` — edit-scope classifier.
- `backend/src/paperhub/llm/prompts/slides_edit_plan_v1.yaml` — deck diff patch plan.
- `backend/src/paperhub/llm/prompts/slides_edit_frame_v1.yaml` — single-frame rewrite.
- `backend/src/paperhub/llm/prompts/slides_revise_v1.yaml` — compile-error fix (used by the revise closure).

**Backend — modified:**
- `backend/src/paperhub/models/domain.py` — `ReportDecision`, `DeckPatchPlan`.
- `backend/src/paperhub/agents/report_pipeline.py` — `classify_report`, `plan_deck_edit`, `edit_frame`, `revise_tex`.
- `backend/src/paperhub/agents/report_graph.py` — conditional edit/recreate branches; wire the real revise closure.
- `backend/src/paperhub/pipelines/slide_pipeline/notes_shift.py` — port `_resolve_replaced_page_range` + `_shift_speaker_notes_after_frame_edit` from `core.py`.
- `backend/src/paperhub/api/decks.py` — `GET /deck/versions` + `POST /deck/versions/{filename}/restore`.

**Frontend — new:**
- `frontend/present.html` — second Vite entry.
- `frontend/src/present/main.tsx` + `frontend/src/present/PresentPage.tsx` — audience window app.
- `frontend/src/lib/presentChannel.ts` — `BroadcastChannel` wrapper.
- `frontend/src/components/slides/VersionHistory.tsx` — list + restore UI.
- `frontend/src/components/slides/PresenterControls.tsx` — timer + next-slide preview + Present button.

**Frontend — modified:**
- `frontend/src/lib/api.ts` — `listDeckVersions`, `restoreDeckVersion`.
- `frontend/src/components/slides/SlidesPanel.tsx` — presenter mode, broadcast on page change, History button.
- `frontend/src/hooks/useChatStream.ts` / `frontend/src/store/chat.ts` — send `current_view_page` with a `slides` turn.
- `frontend/vite.config.ts` — register the `present.html` input.

---

## Task 1: `ReportDecision` model + `sl_resolve` classifier

**Files:**
- Modify: `backend/src/paperhub/models/domain.py`
- Create: `backend/src/paperhub/llm/prompts/slides_resolve_v1.yaml`
- Modify: `backend/src/paperhub/agents/report_pipeline.py`
- Test: `backend/tests/test_report_classify.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_classify.py
from typing import Any
import pytest
from paperhub.models.domain import ReportDecision
from paperhub.agents.report_pipeline import classify_report
from paperhub.tracing.tracer import Tracer


class _A:
    def __init__(self, obj: Any) -> None: self._o = obj
    async def structured(self, **kw: Any) -> Any: return self._o
    def stream(self, **kw: Any): ...


@pytest.mark.asyncio
async def test_classify_edit_page_from_view(fake_tracer: Tracer) -> None:
    dec = ReportDecision(action="edit", edit_target="page", target_page=5)
    out = await classify_report(adapter=_A(dec), tracer=fake_tracer, model="m",
                                instruction="make this equation bigger", current_view_page=5,
                                deck_outline="1. Intro\n5. Method", has_deck=True)
    assert out.action == "edit" and out.edit_target == "page" and out.target_page == 5


@pytest.mark.asyncio
async def test_classify_create_when_no_deck(fake_tracer: Tracer) -> None:
    # Even if the LLM says edit, no deck → caller coerces to create (tested in graph task).
    dec = ReportDecision(action="create", edit_target=None, target_page=None)
    out = await classify_report(adapter=_A(dec), tracer=fake_tracer, model="m",
                                instruction="make slides", current_view_page=0,
                                deck_outline="", has_deck=False)
    assert out.action == "create"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_report_classify.py -v`
Expected: FAIL — `ReportDecision` / `classify_report` missing.

- [ ] **Step 3: Add the model + prompt + function**

`domain.py`:
```python
class ReportDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "recreate", "edit"]
    edit_target: Literal["deck", "page"] | None = None
    target_page: int | None = None
```

`slides_resolve_v1.yaml`:
```yaml
system: |
  Classify a slides request given the current deck. Output ONLY JSON:
  {"action": "create"|"recreate"|"edit", "edit_target": "deck"|"page"|null,
   "target_page": int|null}.
  Rules:
   - No existing deck → action="create".
   - "start over / remake / regenerate from scratch" → action="recreate".
   - Otherwise an instruction that changes the deck → action="edit".
   - For edit: decide scope from what the user is viewing. If the instruction is
     about THIS slide ("make this bigger", "fix the notation here") → edit_target=
     "page", target_page = the current view page. If they name a slide ("slide 3")
     → target_page=3. If it changes the whole deck ("add a section", "shorten
     results", "change theme") → edit_target="deck", target_page=null.
user: |
  HAS_DECK: {has_deck}
  CURRENT_VIEW_PAGE: {current_view_page}
  DECK OUTLINE (frame titles):
  {deck_outline}

  USER INSTRUCTION: {instruction}
```

`report_pipeline.py`:
```python
from paperhub.models.domain import ReportDecision

async def classify_report(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, instruction: str,
    current_view_page: int, deck_outline: str, has_deck: bool,
) -> ReportDecision:
    async with tracer.step(agent="report", tool="report:resolve", model=model) as step:
        step.record_args({"instruction": instruction, "current_view_page": current_view_page,
                          "has_deck": has_deck})
        dec = await adapter.structured(
            slot="slides_resolve/v1",
            variables={"has_deck": str(has_deck), "current_view_page": current_view_page,
                       "deck_outline": deck_outline, "instruction": instruction},
            response_model=ReportDecision, model=model,
        )
        if not has_deck:
            dec = ReportDecision(action="create", edit_target=None, target_page=None)
        step.record_result(dec.model_dump())
    return dec
```

- [ ] **Step 4: Run test to verify it passes.** Run: `cd backend; uv run pytest tests/test_report_classify.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/models/domain.py backend/src/paperhub/llm/prompts/slides_resolve_v1.yaml backend/src/paperhub/agents/report_pipeline.py backend/tests/test_report_classify.py
git commit -m "feat(slides): ReportDecision + LLM edit-scope classifier"
```

---

## Task 2: Real revise prompt + `revise_tex`

**Files:**
- Create: `backend/src/paperhub/llm/prompts/slides_revise_v1.yaml`
- Modify: `backend/src/paperhub/agents/report_pipeline.py`
- Modify: `backend/src/paperhub/agents/report_graph.py` (replace the Phase-1 `_revise` no-op)
- Test: `backend/tests/test_report_pipeline.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_revise_tex_returns_fixed(fake_tracer) -> None:
    from paperhub.agents.report_pipeline import revise_tex
    fixed = await revise_tex(
        adapter=_StructAdapter(tokens=["\\documentclass{beamer}\\begin{document}\\end{document}"]),
        tracer=fake_tracer, model="m", pdflatex_log="! Undefined control sequence.", tex="broken",
    )
    assert "\\documentclass{beamer}" in fixed
```

- [ ] **Step 2: Run → fail. Step 3: implement.**

`slides_revise_v1.yaml`:
```yaml
system: |
  You fix a Beamer LaTeX document that failed to compile. You are given the
  pdflatex error log and the current source. Output the COMPLETE corrected LaTeX
  document only — no commentary, no markdown fences. Preserve content; fix only
  what breaks compilation (unbalanced braces, undefined commands, bad math).
user: |
  PDFLATEX ERROR LOG (tail):
  {pdflatex_log}

  CURRENT SOURCE:
  {tex}
```

`report_pipeline.py`:
```python
async def revise_tex(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, pdflatex_log: str, tex: str,
) -> str:
    async with tracer.step(agent="report", tool="report:compile_revise", model=model) as step:
        step.record_args({"log_tail": pdflatex_log[-500:]})
        toks: list[str] = []
        async for t in adapter.stream(
            slot="slides_revise/v1",
            variables={"pdflatex_log": pdflatex_log, "tex": tex}, model=model,
        ):
            toks.append(t)
        out = "".join(toks).strip()
        # strip accidental ```latex fences
        if out.startswith("```"):
            out = out.split("\n", 1)[-1].rsplit("```", 1)[0]
        step.record_result({"changed": out != tex})
    return out or tex
```

In `report_graph.py`, replace the `_revise` no-op closure with:
```python
        async def _revise(log: str, cur_tex: str) -> str:
            return await revise_tex(adapter=deps.adapter, tracer=deps.tracer,
                                    model=deps.section_model, pdflatex_log=log, tex=cur_tex)
```
(Import `revise_tex`.)

- [ ] **Step 4: Run → pass.** Run: `cd backend; uv run pytest tests/test_report_pipeline.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/llm/prompts/slides_revise_v1.yaml backend/src/paperhub/agents/report_pipeline.py backend/src/paperhub/agents/report_graph.py backend/tests/test_report_pipeline.py
git commit -m "feat(slides): real compile-revise prompt wired into the loop"
```

---

## Task 3: Speaker-note shift helper (port from `core.py`)

**Files:**
- Create: `backend/src/paperhub/pipelines/slide_pipeline/notes_shift.py`
- Test: `backend/tests/test_notes_shift.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_notes_shift.py
from paperhub.pipelines.slide_pipeline.notes_shift import shift_notes_after_frame_edit


def test_shift_grows_notes_after_edit() -> None:
    # Editing page 2 (a single page) into 2 pages: notes for pages >2 shift +1; page 2 dropped.
    notes = {1: "a", 2: "b", 3: "c", 4: "d"}
    out = shift_notes_after_frame_edit(notes, replaced_lo=2, replaced_hi=2,
                                       old_frame_count=4, new_frame_count=5)
    assert out == {1: "a", 4: "c", 5: "d"}  # page 2 dropped, 3→4, 4→5
```

- [ ] **Step 2: Run → fail. Step 3: port** `_resolve_replaced_page_range` + `_shift_speaker_notes_after_frame_edit` from `reference/paper2slides-plus/src/core.py` into `notes_shift.py` as pure functions (no file I/O — take + return a `dict[int, str]`). Provenance header. Public name `shift_notes_after_frame_edit(notes, *, replaced_lo, replaced_hi, old_frame_count, new_frame_count) -> dict[int,str]` and `resolve_replaced_page_range(frames, frame_number) -> tuple[int,int]`.

- [ ] **Step 4: Run → pass.** Run: `cd backend; uv run pytest tests/test_notes_shift.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/slide_pipeline/notes_shift.py backend/tests/test_notes_shift.py
git commit -m "feat(slides): pure speaker-note shift helper (ported from core.py)"
```

---

## Task 4: Single-frame edit (`edit_frame`) + `DeckPatchPlan` + `plan_deck_edit`

**Files:**
- Modify: `backend/src/paperhub/models/domain.py` (`DeckPatchPlan`)
- Create: `backend/src/paperhub/llm/prompts/slides_edit_frame_v1.yaml`, `slides_edit_plan_v1.yaml`
- Modify: `backend/src/paperhub/agents/report_pipeline.py` (`edit_frame`, `plan_deck_edit`)
- Test: `backend/tests/test_report_edit.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_edit.py
from typing import Any
import pytest
from paperhub.models.domain import DeckPatchPlan
from paperhub.agents.report_pipeline import edit_frame, plan_deck_edit


class _A:
    def __init__(self, obj=None, tokens=None): self._o, self._t = obj, tokens or []
    async def structured(self, **kw): return self._o
    def stream(self, **kw):
        async def g():
            for t in self._t: yield t
        return g()


@pytest.mark.asyncio
async def test_edit_frame_replaces_target(fake_tracer) -> None:
    beamer = ("\\documentclass{beamer}\\begin{document}\n"
              "\\begin{frame}{A}\\end{frame}\n\\begin{frame}{B}\\end{frame}\n\\end{document}")
    new_tex = await edit_frame(
        adapter=_A(tokens=["\\begin{frame}{A bigger}\\end{frame}"]),
        tracer=fake_tracer, model="m", beamer_code=beamer, frame_number=1,
        instruction="make A bigger", response_language="English",
    )
    assert "{A bigger}" in new_tex and "{B}" in new_tex


@pytest.mark.asyncio
async def test_plan_deck_edit_returns_patch(fake_tracer) -> None:
    plan = DeckPatchPlan(operations=[{"op": "insert_after", "index": 3, "intent": "limitations"}])
    out = await plan_deck_edit(adapter=_A(obj=plan), tracer=fake_tracer, model="m",
                               instruction="add a limitations slide", deck_outline="...", plan_json="{}")
    assert out.operations[0]["op"] == "insert_after"
```

- [ ] **Step 2: Run → fail. Step 3: implement.**

`domain.py`:
```python
class DeckPatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[dict[str, Any]]  # [{"op":"replace|insert_after|delete","index":int,"intent":str}]
```

`slides_edit_frame_v1.yaml`:
```yaml
system: |
  You rewrite ONE Beamer frame per the user's instruction. Output ONLY the new
  \begin{frame}...\end{frame} block(s) — you may split into multiple frames.
  Keep math in LaTeX; keep \includegraphics references intact.
user: |
  CURRENT FRAME (1-indexed page {frame_number}):
  {frame_content}

  INSTRUCTION: {instruction}
  Write the replacement frame(s) in {response_language}.
```

`slides_edit_plan_v1.yaml`:
```yaml
system: |
  You plan a minimal diff to an existing deck. Output ONLY JSON:
  {"operations": [{"op": "replace"|"insert_after"|"delete", "index": int, "intent": str}, ...]}.
  index is a 1-indexed frame position. Only touch frames the instruction requires;
  leave the rest untouched.
user: |
  DECK OUTLINE (frame index · title):
  {deck_outline}
  CURRENT PLAN JSON: {plan_json}
  INSTRUCTION: {instruction}
```

`report_pipeline.py`:
```python
from paperhub.models.domain import DeckPatchPlan
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    get_frame_by_number, get_preamble, replace_frame_in_beamer, replace_preamble,
)

async def edit_frame(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, beamer_code: str,
    frame_number: int, instruction: str, response_language: str,
) -> str:
    async with tracer.step(agent="report", tool="report:edit_frame", model=model) as step:
        is_preamble = frame_number == 1
        frame_content = (get_preamble(beamer_code) if is_preamble
                         else get_frame_by_number(beamer_code, frame_number)) or ""
        step.record_args({"frame_number": frame_number, "old_frame": frame_content})
        toks: list[str] = []
        async for t in adapter.stream(
            slot="slides_edit_frame/v1",
            variables={"frame_number": frame_number, "frame_content": frame_content,
                       "instruction": instruction,
                       "response_language": response_language or "the user's language"},
            model=model,
        ):
            toks.append(t)
        new_frame = "".join(toks).strip()
        out = (replace_preamble(beamer_code, new_frame) if is_preamble
               else replace_frame_in_beamer(beamer_code, frame_number, new_frame)) or beamer_code
        step.record_result({"frame_number": frame_number, "new_frame": new_frame})
    return out


async def plan_deck_edit(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, instruction: str,
    deck_outline: str, plan_json: str,
) -> DeckPatchPlan:
    async with tracer.step(agent="report", tool="report:edit_plan", model=model) as step:
        step.record_args({"instruction": instruction})
        plan = await adapter.structured(
            slot="slides_edit_plan/v1",
            variables={"deck_outline": deck_outline, "plan_json": plan_json, "instruction": instruction},
            response_model=DeckPatchPlan, model=model,
        )
        step.record_result({"operations": plan.operations})
    return plan
```

- [ ] **Step 4: Run → pass.** Run: `cd backend; uv run pytest tests/test_report_edit.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/models/domain.py backend/src/paperhub/llm/prompts/slides_edit_*.yaml backend/src/paperhub/agents/report_pipeline.py backend/tests/test_report_edit.py
git commit -m "feat(slides): single-frame edit + deck patch-plan pipeline fns"
```

---

## Task 5: Wire edit/recreate branches into the subgraph

**Files:**
- Modify: `backend/src/paperhub/agents/report_graph.py`
- Test: `backend/tests/test_report_graph_edit.py`

The Phase-1 graph had `sl_resolve` route only `empty`/`create`. Now `sl_resolve` calls `classify_report` (when a deck exists) and routes to `create`/`recreate`/`edit_deck`/`edit_page`.

- [ ] **Step 1: Write the failing test** — seed a deck, send an edit instruction with `current_view_page`, stub the classifier to return `edit/page/2`, stub `edit_frame` via the adapter, fake-compile, assert the deck `tex` changed + a new version snapshot exists + speaker notes shifted. Mirror `test_report_graph.py` fixtures.

```python
# backend/tests/test_report_graph_edit.py — skeleton
@pytest.mark.asyncio
async def test_edit_page_updates_deck_and_snapshots(fake_tracer, migrated_db, tmp_path, monkeypatch) -> None:
    # 1) Phase-1 create a deck (reuse the create happy-path setup), or seed decks row + deck.tex on disk.
    # 2) Adapter.structured returns ReportDecision(action="edit", edit_target="page", target_page=2)
    #    on the resolve slot, and a single frame on the edit_frame slot.
    # 3) monkeypatch compile_with_revise to succeed and bump page_count.
    # 4) Run graph; assert get_deck(...).tex contains the edited frame, and
    #    VersionHistory(slides_dir).list_versions() has >= 2 entries.
    ...
```

- [ ] **Step 2: Run → fail. Step 3: implement the branches.**

In `build_report_subgraph`:
- `_resolve` now: load `_papers`; load existing deck via `get_deck`; if a deck exists call `classify_report` with `current_view_page = state.get("current_view_page", 0)`, `deck_outline` = frame titles from `extract_frames_from_beamer(deck.tex)` (read `deck.tex_path`), `has_deck=True`; else `ReportDecision(action="create")`. Store `report_decision` + the loaded `deck` on state.
- `_route` returns: `"no_latex"` (guard), `"empty"`, `"create"` (also for `recreate` — recreate first `VersionHistory(...).save_version(old_tex, "before recreate")` then proceeds like create), `"edit_deck"`, `"edit_page"`.
- `_edit_page` node: read `deck.tex`, snapshot frames pre-edit (`extract_frames_from_beamer`), `resolve_replaced_page_range`, call `edit_frame(...)`, write+compile (`compile_with_revise` with the real revise closure), regenerate notes OR shift via `shift_notes_after_frame_edit` (regenerate is simpler + higher quality — call `generate_notes` on the new tex; keep `shift_notes_after_frame_edit` available for a fast path), `VersionHistory.save_version`, `upsert_deck`, emit `deck` event.
- `_edit_deck` node: `plan_deck_edit(...)` → for each `replace`/`insert_after` op run `generate_section` to produce the frame, splice with `replace_frame_in_beamer` / insert after index; `delete` removes the frame; reassemble (or splice directly into existing `deck.tex` preserving preamble), compile, notes, snapshot, upsert, emit.
- Refactor the Phase-1 `_generate` so `assemble`+`compile`+`notes`+`persist`+`emit` is a shared helper `_finalize_deck(state, tex, plan, papers)` reused by create/recreate/edit paths (DRY).

Add the conditional edges:
```python
    g.add_conditional_edges("sl_resolve", _route, {
        "no_latex": "sl_no_latex", "empty": "sl_empty", "create": "sl_generate",
        "edit_deck": "sl_edit_deck", "edit_page": "sl_edit_page",
    })
    g.add_edge("sl_edit_deck", END)
    g.add_edge("sl_edit_page", END)
```

- [ ] **Step 4: Run → pass.** Run: `cd backend; uv run pytest tests/test_report_graph_edit.py tests/test_report_graph.py -v`

- [ ] **Step 5: Backend gate.** Run: `cd backend; uv run pytest -q; uv run ruff check src tests; uv run mypy src`

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/agents/report_graph.py backend/tests/test_report_graph_edit.py
git commit -m "feat(slides): edit (page/deck) + recreate branches in the subgraph"
```

---

## Task 6: Version-history REST

**Files:**
- Modify: `backend/src/paperhub/api/decks.py`
- Test: `backend/tests/test_decks_versions_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_decks_versions_api.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_list_and_restore_versions(app_with_db, tmp_path) -> None:
    app, conn = app_with_db
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES"); await conn.commit()
    slides_dir = tmp_path / "chat_session" / "1" / "slides"; slides_dir.mkdir(parents=True)
    (slides_dir / "deck.tex").write_text("\\documentclass{beamer}\\begin{document}\\end{document}")
    from paperhub.pipelines.slide_pipeline.history import VersionHistory
    vh = VersionHistory(str(slides_dir))
    vh.save_version("v1 tex", "first", {"1": "n1"})
    vh.save_version("v2 tex", "second", {"1": "n2"})
    from paperhub.db.decks import upsert_deck
    await upsert_deck(conn, session_id=1, run_id=None, tex_path=str(slides_dir/"deck.tex"),
                      pdf_path=None, speaker_notes={}, plan={}, page_count=0,
                      theme="metropolis", contributing_paper_ids=[], status="ok")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        vers = (await c.get("/sessions/1/deck/versions")).json()
        assert len(vers) == 2
        fn = vers[-1]["filename"]  # oldest
        r = await c.post(f"/sessions/1/deck/versions/{fn}/restore")
        assert r.status_code == 200
        assert (slides_dir / "deck.tex").read_text() == "v1 tex"
```

(Set `PAPERHUB_WORKSPACE=tmp_path` for the app fixture so `slides_dir` matches the workspace layout, OR have the restore endpoint derive the slides dir from `decks.tex_path`'s parent. Prefer deriving from `tex_path` parent — robust regardless of workspace env.)

- [ ] **Step 2: Run → fail. Step 3: implement** in `api/decks.py`:

```python
from paperhub.pipelines.slide_pipeline.history import VersionHistory
from paperhub.pipelines.slide_pipeline import compile as compile_mod
from paperhub.db.decks import upsert_deck

@router.get("/sessions/{session_id}/deck/versions")
async def list_versions(session_id: int) -> list[dict[str, Any]]:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
    if deck is None:
        raise HTTPException(404, "no deck")
    slides_dir = str(Path(deck.tex_path).parent)
    return VersionHistory(slides_dir).list_versions()

@router.post("/sessions/{session_id}/deck/versions/{filename}/restore")
async def restore_version(session_id: int, filename: str) -> dict[str, Any]:
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        deck = await get_deck(conn, session_id=session_id)
        if deck is None:
            raise HTTPException(404, "no deck")
        slides_dir = Path(deck.tex_path).parent
        ok = VersionHistory(str(slides_dir)).restore_version(filename, str(slides_dir / "deck.tex"))
        if not ok:
            raise HTTPException(404, "version not found")
        # recompile to regenerate the PDF (no LLM revise on restore — it compiled before)
        tex = (slides_dir / "deck.tex").read_text(encoding="utf-8")
        async def _noop(_log: str, t: str) -> str: return t
        res = await compile_mod.compile_with_revise(
            tex=tex, workdir=slides_dir, tex_name="deck.tex", revise=_noop, max_retries=0)
        import json as _json
        notes_path = slides_dir / "speaker_notes.json"
        notes = _json.loads(notes_path.read_text()) if notes_path.exists() else {}
        await upsert_deck(conn, session_id=session_id, run_id=deck.run_id,
                          tex_path=str(slides_dir/"deck.tex"),
                          pdf_path=str(slides_dir/"deck.pdf") if res.ok else None,
                          speaker_notes=notes, plan=deck.plan, page_count=res.page_count,
                          theme=deck.theme, contributing_paper_ids=deck.contributing_paper_ids,
                          status="ok" if res.ok else "error")
    return {"ok": True, "page_count": res.page_count, "status": "ok" if res.ok else "error"}
```

- [ ] **Step 4: Run → pass.** Run: `cd backend; uv run pytest tests/test_decks_versions_api.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/api/decks.py backend/tests/test_decks_versions_api.py
git commit -m "feat(slides): version history REST (list + restore)"
```

---

## Task 7: Frontend — send `current_view_page` with a slides turn

**Files:**
- Modify: `frontend/src/store/chat.ts` (or wherever the chat request body is built) + `frontend/src/hooks/useChatStream.ts`
- Modify: `frontend/src/lib/sse.ts` (ChatRequestBody type)
- Test: `frontend/tests/hooks/useChatStream.viewpage.test.ts`

- [ ] **Step 1: Write the failing test** — assert that when the slides panel is open on page 4, the POST `/chat` body includes `current_view_page: 4`. Mock `streamChat` and inspect the body.

- [ ] **Step 2: Run → fail. Step 3: implement.**
  - Add `current_view_page?: number` to `ChatRequestBody` in `sse.ts`.
  - When building the chat request in `useChatStream` (the `send` path), read `useSlidesStore.getState()`: if `open` and a deck exists for the backend session, include `current_view_page = currentPageBySession[sid] ?? 1`.
  - Backend `ChatRequest` (in `chat.py`) gains an optional `current_view_page: int = 0` and writes it into `AgentState["current_view_page"]` before running the graph. (Add a tiny backend test asserting it lands in state, or fold into the existing chat request test.)

- [ ] **Step 4: Run → pass.** Run: `cd frontend; npx vitest run tests/hooks/useChatStream.viewpage.test.ts` and `cd backend; uv run pytest tests/test_chat_slides_sse.py -v`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/chat.ts frontend/src/hooks/useChatStream.ts frontend/src/lib/sse.ts backend/src/paperhub/api/chat.py frontend/tests backend/tests
git commit -m "feat(slides): send current_view_page so the agent resolves edit scope"
```

---

## Task 8: Frontend — version-history UI

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/slides/VersionHistory.tsx`
- Modify: `frontend/src/components/slides/SlidesPanel.tsx` (History button → toggles the panel)
- Test: `frontend/tests/components/VersionHistory.test.tsx`

- [ ] **Step 1: Write the failing test** — render `VersionHistory`, MSW returns two versions, click Restore on one → asserts `restoreDeckVersion` called + a refetch of the deck.

- [ ] **Step 2: Run → fail. Step 3: implement.**
  - `api.ts`: `listDeckVersions(sessionId)` → `GET /sessions/{id}/deck/versions`; `restoreDeckVersion(sessionId, filename)` → `POST .../restore`.
  - `VersionHistory.tsx`: list rows (timestamp + description) with a Restore button; on restore, call the API, then re-fetch deck PDF + metadata (`getDeck` + bump a `pdfNonce` so `SlidesPanel` reloads the PDF bytes).
  - `SlidesPanel`: a `[⟳ History]` header button toggles a `historyOpen` state rendering `<VersionHistory sessionId=...>` as an overlay/drawer within the panel.

- [ ] **Step 4: Run → pass.** Run: `cd frontend; npx vitest run tests/components/VersionHistory.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/slides/VersionHistory.tsx frontend/src/components/slides/SlidesPanel.tsx frontend/tests/components/VersionHistory.test.tsx
git commit -m "feat(slides): version history UI (list + restore)"
```

---

## Task 9: Presentation mode — `present.html` entry + BroadcastChannel

**Files:**
- Create: `frontend/present.html`, `frontend/src/present/main.tsx`, `frontend/src/present/PresentPage.tsx`
- Create: `frontend/src/lib/presentChannel.ts`
- Create: `frontend/src/components/slides/PresenterControls.tsx`
- Modify: `frontend/vite.config.ts`, `frontend/src/components/slides/SlidesPanel.tsx`
- Test: `frontend/tests/lib/presentChannel.test.ts`, `frontend/tests/components/PresenterControls.test.tsx`

- [ ] **Step 1: Write the failing test for the channel**

```typescript
// frontend/tests/lib/presentChannel.test.ts
import { describe, it, expect, vi } from "vitest";
import { createPresentChannel } from "@/lib/presentChannel";

describe("presentChannel", () => {
  it("broadcasts and receives page changes", () => {
    const a = createPresentChannel(7);
    const b = createPresentChannel(7);
    const seen: number[] = [];
    b.onPage((p) => seen.push(p));
    a.postPage(3);
    // BroadcastChannel is synchronous within the same context in jsdom polyfill
    expect(seen).toContain(3);
    a.close(); b.close();
  });
});
```
(If jsdom lacks `BroadcastChannel`, add a polyfill in `tests/setup.ts`: a tiny in-memory channel keyed by name. Note this in the test file.)

- [ ] **Step 2: Run → fail. Step 3: implement.**

`presentChannel.ts`:
```typescript
export interface PresentChannel {
  postPage: (page: number) => void;
  onPage: (cb: (page: number) => void) => void;
  close: () => void;
}
export function createPresentChannel(sessionId: number): PresentChannel {
  const ch = new BroadcastChannel(`paperhub-present-${sessionId}`);
  return {
    postPage: (page) => ch.postMessage({ type: "page", page }),
    onPage: (cb) => {
      ch.onmessage = (e) => {
        if (e.data?.type === "page" && typeof e.data.page === "number") cb(e.data.page);
      };
    },
    close: () => ch.close(),
  };
}
```

`present.html` (Vite second entry):
```html
<!doctype html>
<html><head><meta charset="utf-8" /><title>PaperHub — Present</title></head>
<body style="margin:0;background:#000"><div id="present-root"></div>
<script type="module" src="/src/present/main.tsx"></script></body></html>
```

`src/present/main.tsx`: read `?session=N` from `location.search`, mount `<PresentPage sessionId={N}>` into `#present-root`.

`src/present/PresentPage.tsx`: fullscreen black page; fetch `fetchDeckPdfData(sessionId)`; render the current page via react-pdf `<Page width={fit}>`, slide-only, no chrome; subscribe `createPresentChannel(sessionId).onPage(setPage)`; fit width to `window.innerWidth` (resize listener). No notes, no controls.

`vite.config.ts`: add the second input:
```typescript
build: {
  rollupOptions: {
    input: {
      main: path.resolve(__dirname, "index.html"),
      present: path.resolve(__dirname, "present.html"),
    },
  },
},
```

`PresenterControls.tsx`: an elapsed timer (starts when presenting), a next-slide `<Page width={96}>` preview, and a "presenting · synced" badge. Rendered inside `SlidesPanel` when `presenting` is true.

`SlidesPanel.tsx`: a `[▶ Present]` header button → `window.open('/present.html?session=' + sessionId, 'paperhub-present', 'width=1280,height=800')`, set `presenting=true`, create the channel, and `postPage(currentPage)` on every page change (in the `setCurrentPage` effect). On panel page change while presenting, broadcast. Closing/leaving does not close the audience window (it holds its own state).

- [ ] **Step 4: Run → pass.** Run: `cd frontend; npx vitest run tests/lib/presentChannel.test.ts tests/components/PresenterControls.test.tsx`

- [ ] **Step 5: Build check (verifies the second entry compiles).** Run: `cd frontend; npm run build` — expect `dist/present.html` emitted.

- [ ] **Step 6: Commit**

```bash
git add frontend/present.html frontend/src/present/ frontend/src/lib/presentChannel.ts frontend/src/components/slides/PresenterControls.tsx frontend/src/components/slides/SlidesPanel.tsx frontend/vite.config.ts frontend/tests
git commit -m "feat(slides): presentation mode (audience window + BroadcastChannel sync)"
```

---

## Task 10: Q&A-during-talk choreography

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`
- Modify: `frontend/src/store/slides.ts` (persist current page across panel close)
- Test: `frontend/tests/pages/ChatPage.qa.test.tsx`

The behavior: while presenting, the user types a question → normal `paper_qa` turn → the Citation Canvas opens (closing the Slides panel per the shared-slot rule). `currentPageBySession` already persists the page (it's keyed by session and not cleared on close). The audience window is independent (separate window + its own state). When the user reopens Slides, it returns to `currentPageBySession[sid]`.

- [ ] **Step 1: Write the failing test** — render ChatPage with the slides panel open on page 4 and `presenting=true`; simulate a citation-canvas open (call `useCanvasStore.getState().openCitation(1)`); assert `useSlidesStore.getState().open === false` (slot rule) AND `currentPageBySession[sid] === 4` (preserved). Then reopen slides; assert it shows page 4.

- [ ] **Step 2: Run → fail. Step 3: implement.**
  - Extend the existing mutual-exclusion subscriptions in `ChatPage`: when Canvas opens, also `useSlidesStore.getState().closePanel()` (but do NOT reset `currentPageBySession`). When Slides opens, close Canvas + Memory.
  - Ensure `closePanel()` only flips `open=false` and never clears `currentPageBySession` (verify the Phase-1 store — it already only sets `open`).
  - On Slides reopen, `SlidesPanel` reads `currentPageBySession[sid]` (already does).
  - `presenting` stays true across panel close so reopening resumes presenter mode; the audience window is untouched (separate window).

- [ ] **Step 4: Run → pass.** Run: `cd frontend; npx vitest run tests/pages/ChatPage.qa.test.tsx`

- [ ] **Step 5: Full frontend gate.** Run: `cd frontend; npm test; npm run typecheck; npm run lint; npm run build`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/ChatPage.tsx frontend/src/store/slides.ts frontend/tests/pages/ChatPage.qa.test.tsx
git commit -m "feat(slides): Q&A-during-talk choreography (page persists, audience window independent)"
```

---

## Task 11: Manual verification + docs + SRS follow-ups

- [ ] **Step 1: Real-LLM manual check** (with `pdflatex`): generate a deck (Phase 1), then: "make this equation bigger" while viewing slide 5 → only slide 5 changes; "add a limitations slide" → a new slide appears; open History → restore the prior version → deck reverts. Hit Present → a second window opens fullscreen; change slides → it follows. Type an audience question → Canvas answers, Slides closes, audience window holds its page; reopen Slides → back on the same page.

- [ ] **Step 2: Update CLAUDE.md** — mark Plan F **complete** in the plan table (both phases merged); add pointer entries: *"How does slide editing decide page vs deck? → the LLM (`sl_resolve`) reads `current_view_page`; diff edits regenerate only affected frames."* and *"How does presentation page-sync work? → `BroadcastChannel('paperhub-present-<sid>')`; the audience window is a separate `present.html` Vite entry, slide-only."*

- [ ] **Step 3: Add a smoke for edit + present** — extend `smoke_slides.ps1` (or add `smoke_slides_edit.ps1`) to: create a deck, send an edit turn, assert the deck `tex` changed and a version was added.

- [ ] **Step 4: Commit + finish branch**

```bash
git add CLAUDE.md backend/scripts/
git commit -m "docs(slides): Plan F complete — editing + presentation pointers"
```

Then use the **superpowers:finishing-a-development-branch** skill to decide merge/PR (push + merge stay gated on explicit user approval per CLAUDE.md).

---

## Self-review notes (author)

- **Spec coverage:** edit-scope LLM decision ✓ (Task 1), diff edit ✓ (Task 4-5), single-frame + note-shift ✓ (Tasks 3-5), recreate ✓ (Task 5), real revise loop ✓ (Task 2), version-history REST + UI ✓ (Tasks 6, 8), `current_view_page` plumbing ✓ (Task 7), presentation mode + BroadcastChannel + audience window ✓ (Task 9), Q&A-during-talk ✓ (Task 10). All of UC-4's present→ask→answer→resume loop and FR-12's presentation/version-history are covered.
- **Type consistency:** `ReportDecision` (Task 1) consumed by `_resolve` (Task 5); `DeckPatchPlan.operations` op names (`replace`/`insert_after`/`delete`) match between Task 4 prompt, Task 4 model, and Task 5 splice logic; `createPresentChannel` shape identical across Task 9 producer/consumer; `compile_with_revise` reused unchanged from Phase 1 Task 3.
- **Cross-phase contract:** Task 5 refactors the Phase-1 `_generate` into a shared `_finalize_deck` helper — the executor must keep the Phase-1 `test_report_graph.py` green after the refactor (run both edit + create graph tests in Task 5 Step 4).
- **Key risk:** `BroadcastChannel` is absent in jsdom — Task 9 Step 1 notes the test polyfill. The second Vite entry is verified by `npm run build` emitting `dist/present.html` (Task 9 Step 5).
