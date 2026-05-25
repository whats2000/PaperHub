# Plan F4 — Decoupled slide/notes generation + diff-editing (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Supersedes** the decoupling/editing scope of the older `2026-05-23-paperhub-F4-slide-presentation-editing.md`. That doc's **presentation mode + BroadcastChannel + Q&A-during-talk choreography + version-history REST/UI** are **out of scope here** and remain a follow-up (call it F5); they are unchanged by SRS v2.21. This plan implements the v2.21 *decoupling* (the user's actual pain): slides and speaker notes become independent, opt-in operations, with diff-editing and a length budget.

**Goal:** Make a session deck a living artifact — generation produces **slides only**; speaker notes are a **separate, opt-in, independently-languaged** operation; and single-slide / single-note edits are **agent diff-edits** (never full regen) — all routed by a deck-command classifier as ordinary chat turns.

**Architecture:** Keep F3's `report_graph.py` GENERATE pipeline but (a) make `sl_draft` frame-only and stop finalizing notes on generate, (b) persist one `deck_slides` row per final frame, (c) add an LLM **deck-command classifier** in `sl_resolve` that — when a deck already exists — routes the turn to `generate_notes` / `edit_notes` / `edit_slides` / `regenerate` sub-flows. Notes author into `deck_slides.note_text` in an independent `note_language`; `decks.speaker_notes_json` becomes a derived cache. Edits rewrite one targeted frame/note + recompile. A length budget (default 20 min ≈ 15 slides) flows into narrate. The frontend deck chip gains "Generate notes" / "Edit" buttons that simply *send* the corresponding chat turn (no new REST).

**Tech Stack:** Python 3.11 + `uv` (FastAPI, LangGraph, aiosqlite, Pydantic, LiteLLM), React 19 + TS + Vite + Zustand. `pdflatex` for compile.

**Depends on:** F3 (`report_graph.py`, `report_pipeline.py`, `db/decks.py`, `api/decks.py`, `slides` store, `SlidesPanel`, `DeckChip`, deck SSE). SRS **v2.21** — UC-4, FR-12, §III-3 Report Agent row, §III-5.3 (three sub-flows + classifier + length + note decoupling), §III-7 (`deck_slides`).

**Conventions:** TDD per task (failing test → minimal impl → commit). Backend gates from `backend/`: `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src`. Frontend gates from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`. Conventional Commits; body wraps at 72 cols; trailer `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`. Per the global CLAUDE.md, `git push` / PR / merge stay gated on explicit user approval — this plan only commits locally.

> **Real-API gate (CLAUDE.md):** pytest measures syntax/mechanism, not correctness. Run the live `:8000` user-simulation + trace check **once, when this whole plan phase is done** (not per task): check `:8000` is live (ask the user to start it if not — do NOT boot your own), drive `POST /sessions` → `POST /papers` → `POST /chat` with the real scenarios (generate slides → "generate speaker notes" → "把講稿變成繁體中文" → "改第三頁更精簡"), verify each run's trace (`uv run paperhub-replay --run-id <N>`), then ask the user to confirm in the frontend.

---

## File Structure

**Backend — new:**
- `backend/src/paperhub/db/deck_slides.py` — `deck_slides` row CRUD + `rebuild_speaker_notes_json`.
- `backend/src/paperhub/pipelines/slide_pipeline/deck_slides_map.py` — `build_deck_slides(final_tex, page_count)` → per-frame `(slide_index, frame_tex, page_start, page_end)`.
- `backend/src/paperhub/llm/prompts/slides_draft_frame_v1.yaml` — frame-only draft (no note).
- `backend/src/paperhub/llm/prompts/slides_deck_command_v1.yaml` — deck-command classifier.
- `backend/src/paperhub/llm/prompts/slides_note_author_v1.yaml` — rich per-slide speaker note (generate + edit).
- `backend/src/paperhub/llm/prompts/slides_edit_frame_v1.yaml` — single-frame rewrite.

**Backend — modified:**
- `backend/src/paperhub/db/schema.sql` — add `deck_slides` CREATE TABLE.
- `backend/src/paperhub/db/migrate.py` — note the new table (created by schema.sql; no column-add needed).
- `backend/src/paperhub/models/domain.py` — `FrameDraft`, `SlideBudget`, `DeckCommand`; `AgentState` fields (`report_budget`, `report_command`).
- `backend/src/paperhub/agents/report_pipeline.py` — `draft_frame`, `classify_deck_command`, `author_note`, `edit_frame`, `parse_slide_budget`.
- `backend/src/paperhub/agents/report_graph.py` — slides-only GENERATE + `deck_slides` write + hinting finalize; classifier branch in `sl_resolve`; `_route` extension; `sl_notes` / `sl_edit_slides` / `sl_edit_notes` nodes.
- `backend/src/paperhub/llm/prompts/slides_narrate_v1.yaml` — length budget + paper2slides-plus content contract.
- `backend/src/paperhub/llm/prompts/router_v*.yaml` (whichever the router loads) — route deck/notes follow-ups to `slides`.
- `backend/src/paperhub/api/chat.py` — pass `current_view_page` into `AgentState`; ensure `report_stream` threads it.

**Frontend — modified:**
- `frontend/src/lib/sse.ts` — `current_view_page?` on `ChatRequestBody`.
- `frontend/src/hooks/useChatStream.ts` — send `current_view_page` from the slides store.
- `frontend/src/components/slides/DeckChip.tsx` — "Generate notes" / "Edit" buttons that send a chat turn; "no notes yet" affordance.
- `frontend/src/components/chat/MessageBubble.tsx` — pass a `sendTurn` callback to `DeckChip` (or wire via a store action).

**Out of scope (future F5, see the old F4 doc):** presentation mode (`present.html`, `BroadcastChannel`, presenter controls), Q&A-during-talk choreography, version-history REST + UI.

---

## Task 1: `deck_slides` schema + migration note

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`
- Modify: `backend/src/paperhub/db/migrate.py`
- Test: `backend/tests/test_deck_slides_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_deck_slides_schema.py
import pytest
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema


@pytest.mark.asyncio
async def test_deck_slides_table_exists(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        async with conn.execute("PRAGMA table_info(deck_slides)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "id", "deck_id", "slide_index", "frame_tex",
        "note_text", "note_language", "page_start", "page_end",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_deck_slides_schema.py -v`
Expected: FAIL — `no such table: deck_slides`.

- [ ] **Step 3: Add the table to `schema.sql`**

Append after the `decks` CREATE TABLE block:

```sql
CREATE TABLE IF NOT EXISTS deck_slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    slide_index INTEGER NOT NULL,            -- logical frame order (0-based)
    frame_tex TEXT NOT NULL,                 -- the \begin{frame}…\end{frame} block
    note_text TEXT,                          -- NULL until the NOTES flow runs (opt-in)
    note_language TEXT,                      -- independent of the deck/slide language
    page_start INTEGER NOT NULL,             -- 1-based PDF page this frame starts on
    page_end INTEGER NOT NULL,               -- 1-based PDF page this frame ends on
    UNIQUE (deck_id, slide_index)
);
```

In `migrate.py`, update the decks comment block to mention the new table (it is created by `schema.sql`'s `CREATE TABLE IF NOT EXISTS`, so no column-add migration is needed):

```python
    # -----------------------------------------------------------------------
    # decks + deck_slides (v2.18 / v2.21, Plan F): created by schema.sql's
    # CREATE TABLE IF NOT EXISTS. deck_slides holds one row per final frame
    # (frame_tex + opt-in note_text/note_language + PDF page span). Future
    # column-adds go here, mirroring the chat_sessions.deleted_at pattern.
    # -----------------------------------------------------------------------
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_deck_slides_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/db/schema.sql backend/src/paperhub/db/migrate.py backend/tests/test_deck_slides_schema.py
git commit -m "feat(slides): deck_slides table (per-frame rows for decoupled notes/edits)"
```

---

## Task 2: `deck_slides` DB helpers

**Files:**
- Create: `backend/src/paperhub/db/deck_slides.py`
- Test: `backend/tests/test_deck_slides_db.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_deck_slides_db.py
import pytest
from paperhub.db.connection import open_db
from paperhub.db.migrate import apply_schema
from paperhub.db.decks import upsert_deck, get_deck
from paperhub.db.deck_slides import (
    DeckSlideInput, replace_deck_slides, get_deck_slides,
    update_slide_note, update_slide_frame, rebuild_speaker_notes_json,
)


async def _seed_deck(conn) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    await upsert_deck(
        conn, session_id=1, run_id=None, tex_path="/x/deck.tex", pdf_path=None,
        speaker_notes={}, plan={}, page_count=2, theme="metropolis",
        contributing_paper_ids=[], status="ok",
    )
    deck = await get_deck(conn, session_id=1)
    return deck.id


@pytest.mark.asyncio
async def test_replace_and_get(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        deck_id = await _seed_deck(conn)
        await replace_deck_slides(conn, deck_id=deck_id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="\\begin{frame}{A}\\end{frame}",
                           page_start=1, page_end=1),
            DeckSlideInput(slide_index=1, frame_tex="\\begin{frame}{B}\\end{frame}",
                           page_start=2, page_end=2),
        ])
        rows = await get_deck_slides(conn, deck_id=deck_id)
        assert [r.slide_index for r in rows] == [0, 1]
        assert rows[0].note_text is None and rows[0].page_end == 1


@pytest.mark.asyncio
async def test_note_update_and_rebuild_notes_json(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        deck_id = await _seed_deck(conn)
        await replace_deck_slides(conn, deck_id=deck_id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="f0", page_start=1, page_end=2),
            DeckSlideInput(slide_index=1, frame_tex="f1", page_start=3, page_end=3),
        ])
        await update_slide_note(conn, deck_id=deck_id, slide_index=0,
                                note_text="hello", note_language="English")
        notes = await rebuild_speaker_notes_json(conn, deck_id=deck_id)
        # slide 0 spans pages 1-2: page 1 gets the note, page 2 "(continued)".
        assert notes == {"1": "hello", "2": "(continued)"}
        deck = await get_deck(conn, session_id=1)
        assert deck.speaker_notes == {"1": "hello", "2": "(continued)"}


@pytest.mark.asyncio
async def test_frame_update(tmp_path) -> None:
    async with open_db(str(tmp_path / "t.db")) as conn:
        await apply_schema(conn)
        deck_id = await _seed_deck(conn)
        await replace_deck_slides(conn, deck_id=deck_id, slides=[
            DeckSlideInput(slide_index=0, frame_tex="old", page_start=1, page_end=1),
        ])
        await update_slide_frame(conn, deck_id=deck_id, slide_index=0, frame_tex="new")
        rows = await get_deck_slides(conn, deck_id=deck_id)
        assert rows[0].frame_tex == "new"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_deck_slides_db.py -v`
Expected: FAIL — `paperhub.db.deck_slides` missing.

- [ ] **Step 3: Implement `db/deck_slides.py`**

```python
"""deck_slides CRUD (Plan F4 — SRS v2.21).

One row per final frame of the session's current deck: the frame LaTeX, an
opt-in speaker note in an independent language, and the PDF page span the frame
occupies. `decks.speaker_notes_json` is a DERIVED cache rebuilt from these rows
(kept for the `deck` SSE `has_notes` flag + the GET /deck back-compat shape).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True)
class DeckSlideInput:
    slide_index: int
    frame_tex: str
    page_start: int
    page_end: int
    note_text: str | None = None
    note_language: str | None = None


@dataclass(frozen=True)
class DeckSlideRow:
    id: int
    deck_id: int
    slide_index: int
    frame_tex: str
    note_text: str | None
    note_language: str | None
    page_start: int
    page_end: int


async def replace_deck_slides(
    conn: aiosqlite.Connection, *, deck_id: int, slides: list[DeckSlideInput]
) -> None:
    """Atomically replace all rows for a deck (used on generate + recreate)."""
    await conn.execute("DELETE FROM deck_slides WHERE deck_id = ?", (deck_id,))
    await conn.executemany(
        "INSERT INTO deck_slides (deck_id, slide_index, frame_tex, note_text, "
        "note_language, page_start, page_end) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (deck_id, s.slide_index, s.frame_tex, s.note_text,
             s.note_language, s.page_start, s.page_end)
            for s in slides
        ],
    )
    await conn.commit()


async def get_deck_slides(
    conn: aiosqlite.Connection, *, deck_id: int
) -> list[DeckSlideRow]:
    async with conn.execute(
        "SELECT id, deck_id, slide_index, frame_tex, note_text, note_language, "
        "page_start, page_end FROM deck_slides WHERE deck_id = ? ORDER BY slide_index",
        (deck_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        DeckSlideRow(
            id=r[0], deck_id=r[1], slide_index=r[2], frame_tex=r[3],
            note_text=r[4], note_language=r[5], page_start=r[6], page_end=r[7],
        )
        for r in rows
    ]


async def update_slide_note(
    conn: aiosqlite.Connection, *, deck_id: int, slide_index: int,
    note_text: str, note_language: str,
) -> None:
    await conn.execute(
        "UPDATE deck_slides SET note_text = ?, note_language = ? "
        "WHERE deck_id = ? AND slide_index = ?",
        (note_text, note_language, deck_id, slide_index),
    )
    await conn.commit()


async def update_slide_frame(
    conn: aiosqlite.Connection, *, deck_id: int, slide_index: int, frame_tex: str
) -> None:
    await conn.execute(
        "UPDATE deck_slides SET frame_tex = ? WHERE deck_id = ? AND slide_index = ?",
        (frame_tex, deck_id, slide_index),
    )
    await conn.commit()


async def rebuild_speaker_notes_json(
    conn: aiosqlite.Connection, *, deck_id: int
) -> dict[str, str]:
    """Expand per-slide notes into a {page: note} map and write it onto the
    deck row. A slide spanning pages p..q puts its note on page p and
    "(continued)" on p+1..q (matches the F3 finalize_notes gap behaviour).
    Returns the rebuilt map."""
    rows = await get_deck_slides(conn, deck_id=deck_id)
    notes: dict[str, str] = {}
    for r in rows:
        if r.note_text is None:
            continue
        notes[str(r.page_start)] = r.note_text
        for p in range(r.page_start + 1, r.page_end + 1):
            notes[str(p)] = "(continued)"
    await conn.execute(
        "UPDATE decks SET speaker_notes_json = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (json.dumps(notes, ensure_ascii=False), deck_id),
    )
    await conn.commit()
    return notes


__all__ = [
    "DeckSlideInput", "DeckSlideRow", "replace_deck_slides", "get_deck_slides",
    "update_slide_note", "update_slide_frame", "rebuild_speaker_notes_json",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_deck_slides_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/db/deck_slides.py backend/tests/test_deck_slides_db.py
git commit -m "feat(slides): deck_slides CRUD + derived speaker_notes_json rebuild"
```

---

## Task 3: `build_deck_slides` — final-tex → per-frame page spans

**Files:**
- Create: `backend/src/paperhub/pipelines/slide_pipeline/deck_slides_map.py`
- Test: `backend/tests/test_deck_slides_map.py`

This maps the FINAL compiled tex to one `DeckSlideInput` per frame, with its PDF page span. It reuses the F3 `frame_map` helpers (`map_pages_to_slides`, `group_logical_slides`) + `beamer_helpers.extract_frames_from_beamer`. Both walks are in document order, so frames zip to page groups; a leading `\maketitle` page (no frame block) is handled by tail-anchoring the groups.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_deck_slides_map.py
from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides

_DECK = (
    "\\documentclass{beamer}\n\\begin{document}\n"
    "\\begin{frame}\\titlepage\\end{frame}\n"
    "\\begin{frame}{Intro}\\begin{itemize}\\item a\\end{itemize}\\end{frame}\n"
    "\\begin{frame}{Method}\\begin{itemize}\\item b\\end{itemize}\\end{frame}\n"
    "\\end{document}\n"
)


def test_one_to_one_pages() -> None:
    # 3 frames (title + 2 content), no \pause → 3 PDF pages, 1:1.
    rows = build_deck_slides(_DECK, page_count=3)
    assert [r.slide_index for r in rows] == [0, 1, 2]
    assert [(r.page_start, r.page_end) for r in rows] == [(1, 1), (2, 2), (3, 3)]
    assert "Intro" in rows[1].frame_tex


def test_leading_maketitle_offsets_pages() -> None:
    deck = (
        "\\documentclass{beamer}\n\\begin{document}\n\\maketitle\n"
        "\\begin{frame}{Intro}\\item a\\end{frame}\n"
        "\\begin{frame}{Method}\\item b\\end{frame}\n\\end{document}\n"
    )
    # \maketitle = page 1 (no frame block); 2 content frames = pages 2,3.
    rows = build_deck_slides(deck, page_count=3)
    assert len(rows) == 2
    assert [(r.page_start, r.page_end) for r in rows] == [(2, 2), (3, 3)]
    assert "Intro" in rows[0].frame_tex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_deck_slides_map.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `deck_slides_map.py`**

```python
"""Map a FINAL compiled Beamer deck to one DeckSlideInput per frame, with the
PDF page span each frame occupies (Plan F4 — SRS v2.21).

Frames and page groups are both walked in document order, so they zip 1:1. A
leading \\maketitle page has no \\begin{frame} block, so when there is one more
page group than frame, the groups are tail-anchored to the frames.
"""
from __future__ import annotations

from paperhub.db.deck_slides import DeckSlideInput
from paperhub.pipelines.slide_pipeline.beamer_helpers import (
    extract_frames_from_beamer,
)
from paperhub.pipelines.slide_pipeline.frame_map import (
    group_logical_slides,
    map_pages_to_slides,
)


def build_deck_slides(final_tex: str, page_count: int) -> list[DeckSlideInput]:
    frames = extract_frames_from_beamer(final_tex)  # [(num, content, s, e)]
    groups = group_logical_slides(map_pages_to_slides(final_tex))  # [[page,...]]

    # Tail-anchor: a leading \maketitle page (no frame block) leaves one extra
    # leading group; align the LAST len(frames) groups to the frames.
    if len(groups) >= len(frames) and frames:
        aligned = groups[len(groups) - len(frames):]
    else:
        aligned = groups

    rows: list[DeckSlideInput] = []
    if len(aligned) == len(frames) and frames:
        for idx, ((_num, content, _s, _e), grp) in enumerate(zip(frames, aligned)):
            rows.append(
                DeckSlideInput(
                    slide_index=idx,
                    frame_tex=content,
                    page_start=min(grp),
                    page_end=max(grp),
                )
            )
        return rows

    # Fallback: page-count mismatch (unexpected — \pause is forbidden in drafts).
    # Assign each frame one sequential page; clamp to page_count.
    for idx, (_num, content, _s, _e) in enumerate(frames):
        page = min(idx + 1, max(page_count, 1))
        rows.append(
            DeckSlideInput(
                slide_index=idx, frame_tex=content, page_start=page, page_end=page
            )
        )
    return rows


__all__ = ["build_deck_slides"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_deck_slides_map.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/pipelines/slide_pipeline/deck_slides_map.py backend/tests/test_deck_slides_map.py
git commit -m "feat(slides): build_deck_slides maps final tex to per-frame page spans"
```

---

## Task 4: Frame-only draft (`FrameDraft` + `draft_frame` + prompt)

**Files:**
- Modify: `backend/src/paperhub/models/domain.py`
- Create: `backend/src/paperhub/llm/prompts/slides_draft_frame_v1.yaml`
- Modify: `backend/src/paperhub/agents/report_pipeline.py`
- Test: `backend/tests/test_report_pipeline.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_pipeline.py — add
import pytest
from typing import Any
from paperhub.models.domain import FrameDraft, OutlineSlide
from paperhub.agents.report_pipeline import draft_frame


class _StructA:
    def __init__(self, obj: Any) -> None: self._o = obj
    async def structured(self, **kw: Any) -> Any: return self._o
    def stream(self, **kw: Any): ...


@pytest.mark.asyncio
async def test_draft_frame_returns_frame_only(fake_tracer) -> None:
    fd = FrameDraft(frame="\\begin{frame}{A}\\end{frame}")
    out = await draft_frame(
        deck_title="T",
        slide=OutlineSlide(title="A", goal="g", key_points=["k"]),
        assigned_figure=None, assigned_equation=None, chunks_block="(none)",
        adapter=_StructA(fd), tracer=fake_tracer, model="m",
        response_language="English",
    )
    assert out.frame == "\\begin{frame}{A}\\end{frame}"
    assert not hasattr(out, "note")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_report_pipeline.py::test_draft_frame_returns_frame_only -v`
Expected: FAIL — `FrameDraft` / `draft_frame` missing.

- [ ] **Step 3: Implement the model, prompt, and function**

In `domain.py`, beside `SlideDraft`:

```python
class FrameDraft(BaseModel):
    """A single CONCISE Beamer frame, produced by the F4 frame-only draft
    stage. Speaker notes are authored separately by the opt-in NOTES flow."""

    model_config = ConfigDict(extra="forbid")

    frame: str
```

`slides_draft_frame_v1.yaml` (frame-only; the NOTE rules from `slides_draft_v1.yaml` are dropped):

```yaml
system: |
  You produce ONE slide as a CONCISE Beamer frame. The audience reads the
  slide; the detailed narration lives in a SEPARATE speaker note authored
  later — so keep the frame sparse.

  LANGUAGE: Write ALL natural-language text — the \frametitle and every \item
  bullet — in the language specified in the user message. The source paper may
  be in a different language; TRANSLATE as needed. This is mandatory.
  Keep verbatim (do NOT translate): LaTeX commands and math, the exact figure
  key in \includegraphics{...}, table syntax, [chunk:N] citation markers.

  Output ONLY JSON matching the FrameDraft schema: {"frame": str}.
  FRAME rules:
  - Exactly one \frametitle.
  - AT MOST 4 \item bullets, each at most 12 words — a key point, never a
    full sentence. Prefer QUANTIFIED claims ("14% higher accuracy", not
    "better accuracy").
  - If an ASSIGNED FIGURE is given, place it on its own with EXACTLY:
    \includegraphics[width=0.85\textwidth,height=0.7\textheight,keepaspectratio]{KEY}
    using the assigned key verbatim as KEY (bare stem, no path/extension).
  - If an ASSIGNED EQUATION is given, display it; keep the LaTeX faithful.
  - NEVER use \pause or overlay specifications; NO non-existent figures.
  - Output a single \begin{frame}...\end{frame} block — no preamble, no
    \documentclass, no \begin{document}.
user: |
  DECK TITLE: {deck_title}
  SLIDE GOAL: {slide_goal}
  SLIDE TITLE: {slide_title}
  KEY POINTS:
  {key_points}

  ASSIGNED FIGURE (use this EXACT key with \includegraphics, or empty if none):
  {assigned_figure}
  ASSIGNED EQUATION (display this LaTeX, or empty if none):
  {assigned_equation}

  SUPPORTING CHUNKS (context only — do not invent figures):
  {chunks_block}

  LANGUAGE: Write the frame title and ALL bullet points in {response_language}.
  Translate from the source language as needed — this is mandatory. Keep
  verbatim: LaTeX/math, \includegraphics keys, table syntax, [chunk:N] markers.
  Output ONLY the FrameDraft JSON.
  {memory_context}
```

In `report_pipeline.py`, add `draft_frame` (mirror `draft_slide` but frame-only). Import `FrameDraft`:

```python
async def draft_frame(
    *,
    deck_title: str,
    slide: OutlineSlide,
    assigned_figure: str | None,
    assigned_equation: str | None,
    chunks_block: str,
    adapter: LlmAdapter,
    tracer: Tracer,
    model: str,
    response_language: str,
    memory_context: str = "",
    **kw: object,
) -> FrameDraft:
    async with tracer.step(agent="report", tool="report:draft", model=model) as step:
        step.record_args(
            {
                "slide_title": slide.title,
                "figure_key": slide.figure_key,
                "chunk_ids": slide.chunk_ids,
            }
        )
        draft = await adapter.structured(
            slot="slides_draft_frame/v1",
            variables={
                "deck_title": deck_title,
                "slide_goal": slide.goal,
                "slide_title": slide.title,
                "key_points": "\n".join(f"- {p}" for p in slide.key_points),
                "assigned_figure": assigned_figure or "",
                "assigned_equation": assigned_equation or "",
                "chunks_block": chunks_block,
                "response_language": response_language or "the user's language",
                "memory_context": memory_context,
            },
            response_model=FrameDraft,
            model=model,
        )
        step.record_result({"frame": draft.frame})
    return draft
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_report_pipeline.py::test_draft_frame_returns_frame_only -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/models/domain.py backend/src/paperhub/llm/prompts/slides_draft_frame_v1.yaml backend/src/paperhub/agents/report_pipeline.py backend/tests/test_report_pipeline.py
git commit -m "feat(slides): frame-only draft (FrameDraft) — notes decoupled"
```

---

## Task 5: Length budget (`SlideBudget` + `parse_slide_budget` + narrate prompt)

**Files:**
- Modify: `backend/src/paperhub/models/domain.py`
- Modify: `backend/src/paperhub/agents/report_pipeline.py` (`parse_slide_budget`, `narrate_talk` gains budget vars)
- Modify: `backend/src/paperhub/llm/prompts/slides_narrate_v1.yaml`
- Test: `backend/tests/test_slide_budget.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_slide_budget.py
from paperhub.models.domain import SlideBudget
from paperhub.agents.report_pipeline import parse_slide_budget


def test_default_is_15() -> None:
    b = parse_slide_budget("make slides comparing these papers")
    assert b == SlideBudget(target_slide_count=15, depth="standard")


def test_minutes_map_to_slides() -> None:
    # 20 minutes → round(20 * 0.75) = 15.
    assert parse_slide_budget("a 20 minute talk").target_slide_count == 15
    # 8 minutes → 6.
    assert parse_slide_budget("an 8-minute talk").target_slide_count == 8  # clamp lo


def test_explicit_slide_count_wins() -> None:
    assert parse_slide_budget("make a 25 slide deck").target_slide_count == 25


def test_clamped_range() -> None:
    assert parse_slide_budget("a 60 slide deck").target_slide_count == 30  # clamp hi
    assert parse_slide_budget("a 3 slide deck").target_slide_count == 8    # clamp lo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_slide_budget.py -v`
Expected: FAIL — `SlideBudget` / `parse_slide_budget` missing.

- [ ] **Step 3: Implement**

In `domain.py`:

```python
class SlideBudget(BaseModel):
    """Deck length budget (F4 — SRS v2.21). Default 20 min ≈ 15 slides."""

    model_config = ConfigDict(extra="forbid")

    target_slide_count: int = 15
    depth: str = "standard"  # 'overview' | 'standard' | 'deep'
```

In `report_pipeline.py` (deterministic parse — slide count wins over minutes; clamp 8–30):

```python
import re

_SLIDE_RE = re.compile(r"(\d+)\s*(?:slides?|頁|張|投影片)", re.IGNORECASE)
_MIN_RE = re.compile(r"(\d+)\s*(?:min(?:ute)?s?|分鐘|分)", re.IGNORECASE)


def parse_slide_budget(text: str) -> SlideBudget:
    """Extract a slide-count budget from the user's request. Explicit slide
    count wins; else minutes × 0.75; else default 15. Clamped to [8, 30]."""
    count: int | None = None
    m = _SLIDE_RE.search(text)
    if m:
        count = int(m.group(1))
    else:
        mm = _MIN_RE.search(text)
        if mm:
            count = round(int(mm.group(1)) * 0.75)
    if count is None:
        count = 15
    count = max(8, min(30, count))
    return SlideBudget(target_slide_count=count, depth="standard")
```

Extend `narrate_talk` to accept + pass the budget. Add params `target_slide_count: int = 15, depth: str = "standard"` and pass into `variables`:

```python
            variables={
                "briefs_block": briefs_block,
                "figure_inventory": figure_inventory,
                "response_language": response_language or "the user's language",
                "memory_context": memory_context,
                "target_slide_count": target_slide_count,
                "depth": depth,
            },
```

In `slides_narrate_v1.yaml`, add the budget + content contract. Replace the `system` block's synthesis paragraph and the per-slide rules header with:

```yaml
  If several papers are provided, SYNTHESISE across them into a single thematic
  arc — do NOT make one section per paper. If only one paper is provided, build
  a clean, faithful summary talk.

  LENGTH + STRUCTURE (match a strong academic talk, e.g. paper2slides-plus):
  - Produce CLOSE TO {target_slide_count} slides (depth: {depth}).
  - Slide 1 = title page intent (talk title; the deck assembles author/date/url).
  - Slide 2 = a one-slide EXECUTIVE SUMMARY of the contribution.
  - Then: Introduction (background/motivation) → Proposed Method (the BULK —
    most slides) → Results (QUANTIFIED: name benchmarks + numbers) → Conclusion.
  - Prefer quantified, specific points over abstract ones.
```

And add `{target_slide_count}` / `{depth}` to the `user` block:

```yaml
  TARGET LENGTH: about {target_slide_count} slides (depth: {depth}).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_slide_budget.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/models/domain.py backend/src/paperhub/agents/report_pipeline.py backend/src/paperhub/llm/prompts/slides_narrate_v1.yaml backend/tests/test_slide_budget.py
git commit -m "feat(slides): length budget (default 20min~=15 slides) + content-contract narrate"
```

---

## Task 6: GENERATE produces slides-only + writes `deck_slides` + hinting message

**Files:**
- Modify: `backend/src/paperhub/agents/report_graph.py`
- Test: `backend/tests/test_report_graph.py` (extend the existing create happy-path)

This rewires `_generate`: use `draft_frame` (not `draft_slide`), drop `finalize_notes`, write `deck_slides` rows from `build_deck_slides`, set `speaker_notes={}` on the deck, and emit the hinting finalize message + `has_notes=false`. The length budget comes from `state["report_budget"]` (set by `sl_resolve` in Task 8; default to `SlideBudget()` if absent so this task's tests pass standalone).

- [ ] **Step 1: Update the existing create test's expectations**

In `backend/tests/test_report_graph.py`, the create happy-path currently asserts notes are produced. Change it to assert slides-only:

```python
    # After generate: deck exists, has page_count, NO notes yet, deck_slides written.
    deck = await get_deck(conn, session_id=session_id)
    assert deck is not None and deck.page_count > 0
    assert deck.speaker_notes == {}                      # v2.21: notes are opt-in
    from paperhub.db.deck_slides import get_deck_slides
    rows = await get_deck_slides(conn, deck_id=deck.id)
    assert len(rows) == deck.page_count                  # one row per frame/page
    assert all(r.note_text is None for r in rows)
    assert 'Generated' in result_state["final_response"]
    assert 'speaker notes' in result_state["final_response"].lower()  # the hint
```

(If the existing test stubs the adapter to return `SlideDraft`, update that stub to return `FrameDraft(frame=...)` for the `slides_draft_frame/v1` slot.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_report_graph.py -v`
Expected: FAIL — notes still finalized / no deck_slides written / old message.

- [ ] **Step 3: Edit `_generate`**

(a) Replace the `sl_draft` fan-out call `draft_slide(...)` with `draft_frame(...)` (same args minus none — `draft_frame` has the same signature shape; the result is `FrameDraft` with `.frame`). Update the import + the list type:

```python
    from paperhub.agents.report_pipeline import draft_frame  # if not already imported
    budget = state.get("report_budget") or SlideBudget()
    ...
    # ---- sl_narrate (pass the budget) ----
    outline = await narrate_talk(
        briefs_block=_briefs_block(briefs),
        figure_inventory=_inventory_lines(inv),
        adapter=deps.adapter, tracer=deps.tracer, model=deps.plan_model,
        response_language=lang, memory_context=mem,
        target_slide_count=budget.target_slide_count, depth=budget.depth,
    )
    ...
    drafts: list[FrameDraft] = list(
        await asyncio.gather(
            *[
                draft_frame(
                    deck_title=outline.title, slide=s,
                    assigned_figure=_assigned_figure(s.figure_key),
                    assigned_equation=s.equation,
                    chunks_block=_chunks_block(s.chunk_ids),
                    adapter=deps.adapter, tracer=deps.tracer, model=deps.section_model,
                    response_language=lang, memory_context=mem,
                )
                for s in slides
            ]
        )
    )
```

(`frames = await coherence_pass(frames=[d.frame for d in drafts], ...)` is unchanged — it already reads `.frame`.)

(b) DELETE the `sl_notes_finalize` block and its `notes = (await finalize_notes(...) if result.ok else {})`. Replace with `notes: dict[str, str] = {}`.

(c) In `_persist`, write an empty notes file (so the panel has something to read) + keep the version snapshot:

```python
    def _persist() -> None:
        slides_dir.mkdir(parents=True, exist_ok=True)
        (slides_dir / "speaker_notes.json").write_text(
            json.dumps({}, ensure_ascii=False), encoding="utf-8"
        )
        if result.ok:
            VersionHistory(str(slides_dir)).save_version(
                result.tex, "Generated deck (slides only)", {}
            )
    await asyncio.to_thread(_persist)
```

(d) After `upsert_deck(... speaker_notes={} ...)` and `deck = await get_deck(...)`, write the `deck_slides` rows from the final tex:

```python
    from paperhub.pipelines.slide_pipeline.deck_slides_map import build_deck_slides
    from paperhub.db.deck_slides import replace_deck_slides
    if result.ok:
        await replace_deck_slides(
            deps.conn, deck_id=deck.id,
            slides=build_deck_slides(result.tex, result.page_count),
        )
```

(e) Change the finalize message to HINT next moves:

```python
    final = (
        f'Generated a {deck.page_count}-slide deck — "{outline.title}". '
        "Want speaker notes? Say \"generate speaker notes\" (you can pick a "
        "language). I can also edit any slide — just tell me the page."
        if result.ok
        else (
            "I generated the deck but it failed to compile after retries — "
            "showing the last attempt. Check the Trace panel for the LaTeX error."
        )
    )
```

(`has_notes` in `deck_event` is `bool(notes)` → `False`, correct.) Add `from paperhub.models.domain import SlideBudget, FrameDraft` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_report_graph.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/agents/report_graph.py backend/tests/test_report_graph.py
git commit -m "feat(slides): GENERATE produces slides-only + deck_slides + hinting message"
```

---

## Task 7: `DeckCommand` classifier (model + prompt + `classify_deck_command`)

**Files:**
- Modify: `backend/src/paperhub/models/domain.py`
- Create: `backend/src/paperhub/llm/prompts/slides_deck_command_v1.yaml`
- Modify: `backend/src/paperhub/agents/report_pipeline.py`
- Test: `backend/tests/test_deck_command.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_deck_command.py
from typing import Any
import pytest
from paperhub.models.domain import DeckCommand
from paperhub.agents.report_pipeline import classify_deck_command


class _A:
    def __init__(self, obj: Any) -> None: self._o = obj
    async def structured(self, **kw: Any) -> Any: return self._o
    def stream(self, **kw: Any): ...


@pytest.mark.asyncio
async def test_relanguage_notes(fake_tracer) -> None:
    dec = DeckCommand(action="edit_notes", target_scope="all", note_language="Traditional Chinese")
    out = await classify_deck_command(
        adapter=_A(dec), tracer=fake_tracer, model="m",
        instruction="把講稿變成繁體中文", current_view_page=3, deck_outline="1. Intro",
    )
    assert out.action == "edit_notes" and out.note_language == "Traditional Chinese"


@pytest.mark.asyncio
async def test_edit_current_page(fake_tracer) -> None:
    dec = DeckCommand(action="edit_slides", target_scope="current")
    out = await classify_deck_command(
        adapter=_A(dec), tracer=fake_tracer, model="m",
        instruction="make this slide more concise", current_view_page=3,
        deck_outline="1. Intro\n3. Method",
    )
    assert out.action == "edit_slides" and out.target_scope == "current"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_deck_command.py -v`
Expected: FAIL — `DeckCommand` / `classify_deck_command` missing.

- [ ] **Step 3: Implement**

In `domain.py`:

```python
class DeckCommand(BaseModel):
    """How to interpret a slides turn when a deck already exists (F4, v2.21)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["generate_notes", "edit_notes", "edit_slides", "regenerate"]
    target_scope: Literal["current", "page", "all"] = "all"
    target_page: int | None = None
    note_language: str | None = None  # for generate_notes / edit_notes
```

`slides_deck_command_v1.yaml`:

```yaml
system: |
  A slide deck already exists for this session. Classify the user's follow-up
  into ONE action. Output ONLY JSON matching the DeckCommand schema:
  {"action": "generate_notes"|"edit_notes"|"edit_slides"|"regenerate",
   "target_scope": "current"|"page"|"all", "target_page": int|null,
   "note_language": str|null}.
  Rules:
   - "generate_notes": the user wants speaker notes created (none yet, or
     wants them regenerated). If they name a language ("講稿用英文"), set
     note_language to that language name; else null.
   - "edit_notes": change EXISTING notes — re-language ("把講稿變成繁體中文"
     → note_language="Traditional Chinese"), shorten, rephrase. target_scope
     is usually "all"; "page"/"current" if they point at one slide's note.
   - "edit_slides": change the SLIDES. Scope from what they reference:
     "this slide / 這頁" → "current" (uses the on-screen page);
     "slide 3 / 第三頁" → "page" + target_page=3; a deck-wide change
     ("add a limitations slide", "shorten the results section") → "all".
   - "regenerate": "start over / remake the whole deck from scratch".
   - Default target_scope="all", target_page=null, note_language=null.
user: |
  CURRENT_VIEW_PAGE: {current_view_page}
  DECK OUTLINE (page · title):
  {deck_outline}

  USER INSTRUCTION: {instruction}
  Output ONLY the DeckCommand JSON.
```

In `report_pipeline.py`:

```python
from paperhub.models.domain import DeckCommand

async def classify_deck_command(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, instruction: str,
    current_view_page: int, deck_outline: str,
) -> DeckCommand:
    async with tracer.step(agent="report", tool="report:deck_command", model=model) as step:
        step.record_args({"instruction": instruction, "current_view_page": current_view_page})
        dec = await adapter.structured(
            slot="slides_deck_command/v1",
            variables={
                "instruction": instruction,
                "current_view_page": current_view_page,
                "deck_outline": deck_outline,
            },
            response_model=DeckCommand,
            model=model,
        )
        step.record_result(dec.model_dump())
    return dec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_deck_command.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/models/domain.py backend/src/paperhub/llm/prompts/slides_deck_command_v1.yaml backend/src/paperhub/agents/report_pipeline.py backend/tests/test_deck_command.py
git commit -m "feat(slides): DeckCommand classifier (generate_notes/edit_notes/edit_slides/regenerate)"
```

---

## Task 8: `author_note` + `edit_frame` pipeline fns + prompts

**Files:**
- Create: `backend/src/paperhub/llm/prompts/slides_note_author_v1.yaml`, `slides_edit_frame_v1.yaml`
- Modify: `backend/src/paperhub/agents/report_pipeline.py`
- Test: `backend/tests/test_report_edit_fns.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_edit_fns.py
from typing import Any
import pytest
from paperhub.agents.report_pipeline import author_note, edit_frame


class _Stream:
    def __init__(self, toks: list[str]) -> None: self._t = toks
    async def structured(self, **kw: Any) -> Any: ...
    def stream(self, **kw: Any):
        async def g():
            for t in self._t: yield t
        return g()


@pytest.mark.asyncio
async def test_author_note_returns_text(fake_tracer) -> None:
    out = await author_note(
        adapter=_Stream(["講稿：", "這張投影片說明..."]), tracer=fake_tracer, model="m",
        frame_tex="\\begin{frame}{方法}\\end{frame}", existing_note=None,
        instruction=None, note_language="Traditional Chinese",
    )
    assert "這張投影片" in out


@pytest.mark.asyncio
async def test_edit_frame_rewrites_block(fake_tracer) -> None:
    out = await edit_frame(
        adapter=_Stream(["\\begin{frame}{A concise}\\end{frame}"]),
        tracer=fake_tracer, model="m",
        frame_tex="\\begin{frame}{A}\\begin{itemize}\\item x\\end{itemize}\\end{frame}",
        instruction="make it more concise", response_language="English",
    )
    assert "A concise" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_report_edit_fns.py -v`
Expected: FAIL — functions missing.

- [ ] **Step 3: Implement prompts + functions**

`slides_note_author_v1.yaml`:

```yaml
system: |
  You are a PhD student writing the SPEAKER NOTE for ONE slide — what you would
  actually SAY while presenting it (first person, conversational, explaining
  WHY things matter). 3–6 sentences of rich narration that expands the slide's
  sparse bullets. Do NOT restate the bullets verbatim.

  LANGUAGE: Write the note in the language given in the user message. If an
  EXISTING NOTE is provided in another language, TRANSLATE it. This is
  mandatory. Keep verbatim: LaTeX/math and [chunk:N] markers.
  Output ONLY the note text — no JSON, no headings, no "[SLIDE N]".
user: |
  SLIDE (Beamer frame):
  {frame_tex}

  EXISTING NOTE (rewrite toward the instruction if present, else author fresh):
  {existing_note}
  INSTRUCTION (optional, e.g. "shorten", "re-language"):
  {instruction}

  LANGUAGE: Write the note in {note_language}. Translate as needed — mandatory.
  Output ONLY the note text.
```

`slides_edit_frame_v1.yaml`:

```yaml
system: |
  You rewrite ONE Beamer frame per the user's instruction. Output ONLY the new
  \begin{frame}...\end{frame} block — no preamble, no commentary, no markdown
  fences. Keep math in LaTeX; keep any \includegraphics key intact; never add a
  non-existent figure; never use \pause. Keep the frame CONCISE (≤4 bullets,
  ≤12 words each).
  LANGUAGE: write the frame title + bullets in the language given below.
user: |
  CURRENT FRAME:
  {frame_tex}

  INSTRUCTION: {instruction}
  Write the replacement frame in {response_language}.
```

In `report_pipeline.py` (both stream text out; strip accidental ``` fences for the frame):

```python
async def author_note(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, frame_tex: str,
    existing_note: str | None, instruction: str | None, note_language: str,
) -> str:
    async with tracer.step(agent="report", tool="report:note_author", model=model) as step:
        step.record_args({"note_language": note_language, "has_existing": existing_note is not None})
        toks: list[str] = []
        async for t in adapter.stream(
            slot="slides_note_author/v1",
            variables={
                "frame_tex": frame_tex,
                "existing_note": existing_note or "(none — author fresh)",
                "instruction": instruction or "(none)",
                "note_language": note_language or "the user's language",
            },
            model=model,
        ):
            toks.append(t)
        out = "".join(toks).strip()
        step.record_result({"note": out})
    return out


async def edit_frame(
    *, adapter: LlmAdapter, tracer: Tracer, model: str, frame_tex: str,
    instruction: str, response_language: str,
) -> str:
    async with tracer.step(agent="report", tool="report:edit_frame", model=model) as step:
        step.record_args({"old_frame": frame_tex, "instruction": instruction})
        toks: list[str] = []
        async for t in adapter.stream(
            slot="slides_edit_frame/v1",
            variables={
                "frame_tex": frame_tex,
                "instruction": instruction,
                "response_language": response_language or "the user's language",
            },
            model=model,
        ):
            toks.append(t)
        out = "".join(toks).strip()
        if out.startswith("```"):
            out = out.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        step.record_result({"new_frame": out})
    return out or frame_tex
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_report_edit_fns.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/llm/prompts/slides_note_author_v1.yaml backend/src/paperhub/llm/prompts/slides_edit_frame_v1.yaml backend/src/paperhub/agents/report_pipeline.py backend/tests/test_report_edit_fns.py
git commit -m "feat(slides): author_note + edit_frame pipeline fns + prompts"
```

---

## Task 9: Wire the classifier + NOTES + EDIT sub-flows into the subgraph

**Files:**
- Modify: `backend/src/paperhub/agents/report_graph.py`
- Test: `backend/tests/test_report_graph_subflows.py`

`sl_resolve` now: load papers AND any existing deck; if a deck exists, call `classify_deck_command` and stash `report_command` + route accordingly; else parse the budget into `report_budget` and route `create`. New nodes `sl_notes`, `sl_edit_slides`, `sl_edit_notes`. A shared `_recompile_and_emit(state, tex)` helper compiles, writes `deck_slides`, upserts the deck, and emits the `deck` event (DRY across edit paths).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_graph_subflows.py — skeleton; mirror test_report_graph.py fixtures
import pytest
from paperhub.db.decks import get_deck
from paperhub.db.deck_slides import get_deck_slides
from paperhub.models.domain import DeckCommand


@pytest.mark.asyncio
async def test_generate_notes_fills_notes_without_touching_frames(
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    # 1) Seed a generated deck (reuse the create happy-path helper) → deck_slides
    #    rows with note_text=None, frames F0/F1.
    # 2) Stub adapter: classify_deck_command → DeckCommand(action="generate_notes",
    #    note_language="English"); author_note stream → "note for slide".
    # 3) Run the graph with the seeded session.
    # 4) Assert: every deck_slides.note_text is set, frame_tex UNCHANGED,
    #    deck.speaker_notes non-empty, deck event has_notes=True.
    ...


@pytest.mark.asyncio
async def test_edit_notes_relanguages_only_notes(
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    # Seed a deck WITH English notes. classify → edit_notes/all/Traditional Chinese;
    # author_note → Chinese text. Assert notes change language, frames unchanged,
    # NO recompile (pdf_path unchanged).
    ...


@pytest.mark.asyncio
async def test_edit_slides_page_rewrites_one_frame_and_recompiles(
    fake_tracer, migrated_db, tmp_path, monkeypatch
) -> None:
    # Seed a deck. classify → edit_slides/page/target_page=2; edit_frame → new frame.
    # monkeypatch compile_with_revise to succeed + bump page_count.
    # Assert: deck_slides[1].frame_tex changed, others unchanged, a new version
    # snapshot exists.
    ...
```

(Fill the skeletons using the same fixtures + adapter-stub pattern as `test_report_graph.py`. The stub adapter must return the right object per `slot`: `slides_deck_command/v1` → the `DeckCommand`; `slides_note_author/v1` / `slides_edit_frame/v1` → streamed tokens.)

- [ ] **Step 2: Run → fail.** Run: `cd backend; uv run pytest tests/test_report_graph_subflows.py -v`

- [ ] **Step 3: Implement the routing + nodes**

In `report_graph.py`:

(a) `ReportDeps` gets the already-present models; `_resolve` becomes:

```python
async def _resolve(state: AgentState) -> AgentState:
    papers = await _load_enabled_papers(deps.conn, state["session_id"])  # existing loader
    out: AgentState = {**state, "report_papers": papers}
    if not papers:
        return out
    if not _pdflatex_available():
        return out
    deck = await get_deck(deps.conn, session_id=state["session_id"])
    instruction = effective_query(state) or state.get("user_message", "")
    if deck is not None:
        rows = await get_deck_slides(deps.conn, deck_id=deck.id)
        outline_lines = "\n".join(
            f"{r.page_start}. {_frame_title(r.frame_tex)}" for r in rows
        )
        cmd = await classify_deck_command(
            adapter=deps.adapter, tracer=deps.tracer, model=deps.resolve_model,
            instruction=instruction, current_view_page=state.get("current_view_page", 1),
            deck_outline=outline_lines or "(no slides)",
        )
        out["report_command"] = cmd
    else:
        out["report_budget"] = parse_slide_budget(instruction)
    return out
```

Add a tiny `_frame_title(frame_tex)` helper (regex `\\frametitle\{(.+?)\}` or `\\begin\{frame\}\{(.+?)\}`, fallback `"slide"`).

(b) `_route`:

```python
def _route(state: AgentState) -> str:
    if not state.get("report_papers"):
        return "empty"
    if not _pdflatex_available():
        return "no_latex"
    cmd = state.get("report_command")
    if cmd is None:
        return "create"
    if cmd.action == "regenerate":
        return "create"
    if cmd.action == "generate_notes" or cmd.action == "edit_notes":
        return "notes"
    return "edit_slides"  # action == "edit_slides"
```

(c) Shared helper (module-level closure inside `build_report_subgraph`, like `_generate`):

```python
async def _recompile_and_persist(
    state: AgentState, *, tex: str, deck, outline_plan: dict[str, Any], papers
) -> dict[str, Any]:
    slides_dir = deps.workspace / "chat_session" / str(state["session_id"]) / "slides"
    async def _revise(log: str, cur_tex: str) -> str:
        return await revise_tex(pdflatex_log=log, tex=cur_tex,
                                adapter=deps.adapter, tracer=deps.tracer, model=deps.section_model)
    tex, _rej = verify_and_fix_graphics(tex, allowed_keys=_inventory_keys_for(papers))
    result = await compile_mod.compile_with_revise(
        tex=tex, workdir=slides_dir, tex_name="deck.tex", revise=_revise, max_retries=2)
    await asyncio.to_thread(lambda: VersionHistory(str(slides_dir)).save_version(
        result.tex, "Edited deck", {}) if result.ok else None)
    await upsert_deck(
        deps.conn, session_id=state["session_id"], run_id=state.get("run_id"),
        tex_path=str(slides_dir / "deck.tex"),
        pdf_path=str(slides_dir / "deck.pdf") if result.ok else None,
        speaker_notes=deck.speaker_notes, plan=outline_plan,
        page_count=result.page_count, theme=_THEME,
        contributing_paper_ids=deck.contributing_paper_ids,
        status="ok" if result.ok else "error")
    fresh = await get_deck(deps.conn, session_id=state["session_id"])
    if result.ok:
        await replace_deck_slides(deps.conn, deck_id=fresh.id,
                                  slides=build_deck_slides(result.tex, result.page_count))
        # re-attach notes that survived (edit_slides preserves notes by index)
        await rebuild_speaker_notes_json(deps.conn, deck_id=fresh.id)
    return {"deck": fresh, "ok": result.ok, "page_count": result.page_count}
```

(`_inventory_keys_for(papers)` rebuilds the allowed-key set via `build_inventory`, same as `_generate`. For edit-slides that preserve notes, copy the old rows' `note_text`/`note_language` by `slide_index` onto the rebuilt rows BEFORE `rebuild_speaker_notes_json` — see the `_edit_slides` node.)

(d) `_notes` node (generate_notes / edit_notes — never recompiles, never touches frames):

```python
async def _notes(state: AgentState) -> AgentState:
    cmd: DeckCommand = state["report_command"]
    deck = await get_deck(deps.conn, session_id=state["session_id"])
    rows = await get_deck_slides(deps.conn, deck_id=deck.id)
    lang = cmd.note_language or response_language(state)
    targets = _select_rows(rows, cmd)        # all, or the page/current one
    for r in targets:
        note = await author_note(
            adapter=deps.adapter, tracer=deps.tracer, model=deps.notes_model,
            frame_tex=r.frame_tex,
            existing_note=r.note_text if cmd.action == "edit_notes" else None,
            instruction=state.get("user_message") if cmd.action == "edit_notes" else None,
            note_language=lang)
        await update_slide_note(deps.conn, deck_id=deck.id, slide_index=r.slide_index,
                                note_text=note, note_language=lang)
        await _flush_steps()
    notes = await rebuild_speaker_notes_json(deps.conn, deck_id=deck.id)
    fresh = await get_deck(deps.conn, session_id=state["session_id"])
    _emit_deck(writer, fresh, _papers_meta(state), has_notes=bool(notes))
    verb = "Wrote" if cmd.action == "generate_notes" else "Updated"
    return {**state, "final_response": f"{verb} speaker notes ({lang}). Open the Slides panel to read them."}
```

(e) `_edit_slides` node (rewrite targeted frame(s), recompile, PRESERVE notes by index):

```python
async def _edit_slides(state: AgentState) -> AgentState:
    cmd: DeckCommand = state["report_command"]
    deck = await get_deck(deps.conn, session_id=state["session_id"])
    rows = await get_deck_slides(deps.conn, deck_id=deck.id)
    old_notes = {r.slide_index: (r.note_text, r.note_language) for r in rows}
    full_tex = Path(deck.tex_path).read_text(encoding="utf-8")
    targets = _select_rows(rows, cmd)
    new_tex = full_tex
    for r in targets:
        new_frame = await edit_frame(
            adapter=deps.adapter, tracer=deps.tracer, model=deps.section_model,
            frame_tex=r.frame_tex, instruction=state.get("user_message", ""),
            response_language=response_language(state))
        replaced = replace_frame_in_beamer(new_tex, r.slide_index + 1, new_frame)
        if replaced:
            new_tex = replaced
        await _flush_steps()
    res = await _recompile_and_persist(state, tex=new_tex, deck=deck,
                                       outline_plan=deck.plan, papers=state["report_papers"])
    # restore notes onto matching slide_index rows, then rebuild the page map.
    for r in await get_deck_slides(deps.conn, deck_id=res["deck"].id):
        nt, nl = old_notes.get(r.slide_index, (None, None))
        if nt is not None:
            await update_slide_note(deps.conn, deck_id=res["deck"].id,
                                    slide_index=r.slide_index, note_text=nt, note_language=nl or "")
    notes = await rebuild_speaker_notes_json(deps.conn, deck_id=res["deck"].id)
    fresh = await get_deck(deps.conn, session_id=state["session_id"])
    _emit_deck(writer, fresh, _papers_meta(state), has_notes=bool(notes))
    msg = ("Edited the deck and recompiled." if res["ok"]
           else "Edited the deck but it failed to compile — showing the last attempt.")
    return {**state, "final_response": msg}
```

Add `_select_rows(rows, cmd)`: `all` → rows; `current` → the row whose page span contains `current_view_page`; `page` → the row containing `target_page`. Add `_emit_deck(writer, deck, papers_meta, has_notes)` factored from `_generate`'s deck-event block. Wire nodes + edges:

```python
    g.add_node("sl_notes", _notes)
    g.add_node("sl_edit_slides", _edit_slides)
    g.add_conditional_edges("sl_resolve", _route, {
        "empty": "sl_empty", "no_latex": "sl_no_latex", "create": "sl_generate",
        "notes": "sl_notes", "edit_slides": "sl_edit_slides",
    })
    g.add_edge("sl_notes", END)
    g.add_edge("sl_edit_slides", END)
```

Add `AgentState` fields in `domain.py`:

```python
    report_budget: SlideBudget        # v2.21: GENERATE length budget
    report_command: DeckCommand       # v2.21: deck-scoped follow-up action
```

- [ ] **Step 4: Run → pass.** Run: `cd backend; uv run pytest tests/test_report_graph_subflows.py tests/test_report_graph.py -v`

- [ ] **Step 5: Backend gate.** Run: `cd backend; uv run pytest -q; uv run ruff check src tests; uv run mypy src`

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/agents/report_graph.py backend/src/paperhub/models/domain.py backend/tests/test_report_graph_subflows.py
git commit -m "feat(slides): NOTES + EDIT sub-flows + deck-command routing in the subgraph"
```

---

## Task 10: Route deck/notes follow-ups to the `slides` intent

**Files:**
- Modify: the router prompt YAML the router loads (find via `grep -rl "intent" backend/src/paperhub/llm/prompts/router*`)
- Test: `backend/tests/test_router.py` (extend) — only if the router has a stubbable classify test; else verify via the real-API gate.

The deck-command classifier only runs after the router picks `slides`. So the router must route deck follow-ups ("generate speaker notes", "把講稿變成繁體中文", "改第三頁", "edit this slide") to `slides`.

- [ ] **Step 1: Add a failing router test (if the suite stubs the router classifier)**

```python
# backend/tests/test_router.py — add (adapt to the existing router test harness)
@pytest.mark.asyncio
async def test_deck_followups_route_to_slides(fake_tracer) -> None:
    for msg in ["把講稿變成繁體中文", "generate speaker notes", "改第三頁更精簡"]:
        dec = await _classify(msg, history=[{"role": "assistant", "content": "Generated a 15-slide deck."}])
        assert dec.intent == "slides", msg
```

(If the router test uses a real LLM and can't be stubbed deterministically, SKIP this step and rely on the real-API gate in Task 13.)

- [ ] **Step 2: Run → fail (or skip per above).**

- [ ] **Step 3: Edit the router prompt** — in the `slides` intent description, add deck-follow-up cues:

```
- slides: the user wants to CREATE, EDIT, or add SPEAKER NOTES to a slide deck
  / talk / presentation. This includes follow-ups about an existing deck:
  "generate speaker notes", "把講稿變成繁體中文" (re-language the notes),
  "edit this slide / 改第三頁", "make the deck shorter", "redo the slides".
  If the recent history shows a deck was generated and the turn is about the
  talk / slides / speaker notes (講稿), choose slides.
```

- [ ] **Step 4: Run → pass (or skip).**

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/llm/prompts/ backend/tests/test_router.py
git commit -m "feat(router): route deck/notes follow-ups to the slides intent"
```

---

## Task 11: Pass `current_view_page` from the frontend into the agent

**Files:**
- Modify: `backend/src/paperhub/api/chat.py`
- Modify: `frontend/src/lib/sse.ts`, `frontend/src/hooks/useChatStream.ts`
- Test: `backend/tests/test_chat_slides_sse.py` (extend) + `frontend/tests/hooks/useChatStream.viewpage.test.ts`

- [ ] **Step 1: Write the failing backend test**

```python
# backend/tests/test_chat_slides_sse.py — add
@pytest.mark.asyncio
async def test_current_view_page_lands_in_state(app_with_db) -> None:
    # POST /chat with current_view_page=4; assert the AgentState built for the
    # report subgraph carries current_view_page=4 (monkeypatch report_stream to
    # capture the state, or assert via a slides turn trace). Mirror the existing
    # SSE test's request shape; add "current_view_page": 4 to the body.
    ...
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.**
  - In `chat.py`, add `current_view_page: int = 0` to the `ChatRequest` Pydantic model, and when building `AgentState` set `"current_view_page": req.current_view_page`.
  - In `sse.ts`:

```typescript
export interface ChatRequestBody {
  session_id: number | null;
  user_message: string;
  history: { role: "user" | "assistant"; content: string }[];
  current_view_page?: number;
}
```

  - In `useChatStream.ts`, where the body is built, read the slides store and include the field when the panel is showing this session's deck:

```typescript
import { useSlidesStore } from "@/store/slides";
// ...inside send(), after backendSessionId is known:
const slides = useSlidesStore.getState();
const currentViewPage =
  backendSessionId !== null && slides.deckBySession[backendSessionId]
    ? (slides.currentPageBySession[backendSessionId] ?? 1)
    : undefined;
await streamChat(
  {
    session_id: backendSessionId,
    user_message: userMessage,
    history,
    ...(currentViewPage !== undefined ? { current_view_page: currentViewPage } : {}),
  },
  // ...
);
```

- [ ] **Step 4: Run → pass.** Run: `cd backend; uv run pytest tests/test_chat_slides_sse.py -v` and `cd frontend; npx vitest run tests/hooks/useChatStream.viewpage.test.ts`

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/api/chat.py frontend/src/lib/sse.ts frontend/src/hooks/useChatStream.ts backend/tests/test_chat_slides_sse.py frontend/tests/hooks/useChatStream.viewpage.test.ts
git commit -m "feat(slides): thread current_view_page into the report agent for edit scope"
```

---

## Task 12: Deck chip affordances — "Generate notes" / "Edit" send chat turns

**Files:**
- Modify: `frontend/src/components/slides/DeckChip.tsx`
- Modify: `frontend/src/components/chat/MessageBubble.tsx`
- Test: `frontend/tests/components/DeckChip.test.tsx`

The chip's new buttons compose a chat message and send it through the normal stream (the deck-command classifier handles the rest). `DeckChip` takes an `onSend(message: string)` prop; `MessageBubble` passes a callback that calls the chat-send hook for the active session.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/components/DeckChip.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DeckChip } from "@/components/slides/DeckChip";
import type { DeckEventData } from "@/types/domain";

const deck: DeckEventData = {
  deck_id: 1, session_id: 7, page_count: 15, title: "T",
  status: "ok", contributing_papers: [{ id: 1 }], has_notes: false,
};

describe("DeckChip", () => {
  it("shows 'Generate notes' when has_notes is false and sends a turn", () => {
    const onSend = vi.fn();
    render(<DeckChip deck={deck} onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: /generate.*notes/i }));
    expect(onSend).toHaveBeenCalledWith(expect.stringMatching(/speaker notes/i));
  });

  it("shows 'Edit notes' when has_notes is true", () => {
    const onSend = vi.fn();
    render(<DeckChip deck={{ ...deck, has_notes: true }} onSend={onSend} />);
    expect(screen.getByRole("button", { name: /edit notes/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run → fail.** Run: `cd frontend; npx vitest run tests/components/DeckChip.test.tsx`

- [ ] **Step 3: Implement.**
  - `DeckChip` gains `onSend?: (message: string) => void` in `Props`. Add two buttons after the existing Open/Download actions:

```tsx
{deck.status === "ok" && onSend && (
  <>
    <Button
      type="button" size="sm" variant="ghost"
      className="h-7 px-2 text-xs"
      onClick={() => onSend(deck.has_notes
        ? "Edit the speaker notes for this deck"
        : "Generate speaker notes for this deck")}
      aria-label={deck.has_notes ? "Edit notes" : "Generate notes"}
    >
      {deck.has_notes ? "Edit notes" : "Generate notes"}
    </Button>
    <Button
      type="button" size="sm" variant="ghost"
      className="h-7 px-2 text-xs"
      onClick={() => onSend("Edit this slide")}
      aria-label="Edit slide"
    >
      Edit
    </Button>
  </>
)}
```

  - `MessageBubble` passes `onSend`. The chat-send entrypoint is the same hook `ChatPage` uses (the `send` from `useChatStream` or a store action). Thread a callback down: `MessageBubble` accepts an `onSendTurn?: (msg: string) => void` prop (added where `ChatPage` renders the message list, wired to the existing send path with the active session id), and renders:

```tsx
{isAssistant && message.deck !== undefined && (
  <DeckChip deck={message.deck} onSend={onSendTurn} />
)}
```

(If `MessageBubble` has no send access today, add the `onSendTurn` prop at the `ChatPage` → `MessageList` → `MessageBubble` boundary, passing the same `send(activeSessionId, msg)` ChatPage already uses for the composer. Do NOT call the store directly from the chip.)

- [ ] **Step 4: Run → pass.** Run: `cd frontend; npx vitest run tests/components/DeckChip.test.tsx`

- [ ] **Step 5: Frontend gate.** Run: `cd frontend; npm test; npm run typecheck; npm run lint; npm run build`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/slides/DeckChip.tsx frontend/src/components/chat/MessageBubble.tsx frontend/tests/components/DeckChip.test.tsx
git commit -m "feat(slides): deck chip Generate-notes / Edit affordances (send chat turns)"
```

---

## Task 13: Real-API verification + docs

- [ ] **Step 1: Backend + frontend gates green.** From `backend/`: `uv run pytest -q; uv run ruff check src tests; uv run mypy src`. From `frontend/`: `npm test; npm run typecheck; npm run lint; npm run build`.

- [ ] **Step 2: Real-API user-simulation (CLAUDE.md gate — once, now that the plan phase is done).**
  Check `:8000` is live (`curl -s -m 3 http://127.0.0.1:8000/health`); if not, ASK the user to start it (do NOT boot your own). Then drive, as a user would (`POST /sessions` → `POST /papers` for a paper → `POST /chat`):
  1. *"Make a 20-minute talk from this paper"* → expect a deck with ~15 slides, **no notes**, a hinting final message.
  2. *"Generate speaker notes"* → notes appear (`has_notes=true`), frames unchanged.
  3. *"把講稿變成繁體中文"* → notes re-languaged to Traditional Chinese, **slides still English**, **no recompile** (same `pdf_path`).
  4. *"把第三頁變得更精簡"* (while viewing page 3) → only slide 3's frame changes, deck recompiles, notes for other slides preserved.
  For each, verify the trace: `uv run paperhub-replay --run-id <N>` (right stages fired — `report:deck_command`, `report:note_author` / `report:edit_frame`, `report:compile` only on slide edits; `status=ok`; recorded state matches).

- [ ] **Step 3: Ask the user to confirm in the frontend** — generate → chip shows "Generate notes" → click → notes appear in the Slides panel; re-language via chat → notes change, slides don't; edit a page → that slide updates. Note any `:8000` restart (backend code) or frontend rebuild needed.

- [ ] **Step 4: Update CLAUDE.md.** In the Plan F table row, mark **F4 (decoupled generation/notes/editing) shipped**; add pointer entries:
  - *"Why doesn't 'convert the notes' regenerate the slides now? → v2.21: GENERATE is slides-only; notes/edits are decoupled deck-command sub-flows (NOTES authors `deck_slides.note_text` in an independent language; EDIT diff-edits one frame). See SRS §III-5.3 + plan F4."*
  - *"How is slide length controlled? → `parse_slide_budget` (default 20 min ≈ 15 slides, clamp 8–30) → narrate budget."*

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: Plan F4 shipped — decoupled slide/notes generation + diff-editing"
```

Then use **superpowers:finishing-a-development-branch** to decide merge/PR (push + merge stay gated on explicit user approval per CLAUDE.md).

---

## Self-review notes (author)

- **Spec coverage (SRS v2.21):** decouple slides⟂notes ✓ (Tasks 4, 6 — frame-only draft, notes dropped from GENERATE); deck-scoped classifier ✓ (Tasks 7, 9); opt-in notes + hinting message ✓ (Task 6); independent note language ✓ (Tasks 8, 9 — `note_language` on `author_note` + `deck_slides`); per-slide `deck_slides` rows + derived `speaker_notes_json` ✓ (Tasks 1–3, 9); length budget ✓ (Task 5); style discipline ✓ (Task 5 narrate contract + Task 4 frame rules); diff-edit (never full regen) ✓ (Tasks 8, 9 — `edit_frame` + targeted recompile, notes preserved by index); chat-driven affordances, no new REST ✓ (Tasks 10–12). Router routing of follow-ups ✓ (Task 10). `current_view_page` plumbing ✓ (Task 11).
- **Explicitly out of scope (future F5):** presentation mode, BroadcastChannel, Q&A-during-talk, version-history REST/UI — unchanged by v2.21; the old F4 doc's Tasks 6/8/9/10 cover them.
- **Type consistency:** `FrameDraft.frame` (Task 4) consumed by `_generate` + `coherence_pass([d.frame ...])` (Task 6); `DeckCommand{action, target_scope, target_page, note_language}` identical across Task 7 model, Task 7 prompt, Task 9 `_route`/`_select_rows`; `DeckSlideInput`/`DeckSlideRow` fields (Task 2) used by `build_deck_slides` (Task 3) + the graph nodes (Task 9); `SlideBudget{target_slide_count, depth}` (Task 5) → `state["report_budget"]` → `narrate_talk` (Task 6/9). `rebuild_speaker_notes_json` returns + persists the `{page: note}` map consumed by the existing `SlidesPanel` `speakerNotes` prop (unchanged).
- **Key risks:** (1) `build_deck_slides` page-span alignment when a deck uses `\maketitle` vs a title frame — handled by tail-anchoring + a fallback (Task 3, both tested). (2) The router must pick `slides` for bare follow-ups — Task 10; if the router LLM can't be stubbed, the real-API gate (Task 13) is the proof. (3) `edit_slides` preserving notes across a recompile that changes page spans — handled by copying notes by `slide_index` then `rebuild_speaker_notes_json` (Task 9); if a frame split changes slide_index counts, surplus notes are dropped (acceptable — the user can regenerate notes).
