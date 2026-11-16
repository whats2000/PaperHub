# H5b.1 — Notes Layer Core Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reader can mark a paper (highlights, quotes, figures, equations, boxes, highlighter strokes, marks on translations), keep what they learn in one note per paper organised by its sections — edited in a Notion-style editor beside the reader — and the core agent can read, search and edit those notes without ever overwriting the user's words; notes export as Markdown or `.docx`.

**Architecture:** A new `paperhub.notes` package stores notes as ordered trees of blocks with stable ids, revisions, per-block history, authorship and FTS; every change (user typing burst, agent change set, migration) goes through one op engine that bumps the note's revision and turns an agent change to a user block into a suggestion. Marks are anchors (pieces of quoted text, PDF line boxes or page boxes) owned by a paper-quote block; the readers draw them as overlays, never touching `source.html`, `chunks` or the PDF. The frontend adds a Notes pane (Reader | Notes tabs, side by side when wide) running a BlockNote editor with PaperHub's custom blocks, reader overlays and a note card placed like the PDF translation card.

**Tech Stack:** FastAPI + SQLite (`db/schema.sql` + `db/migrate.py`, FTS5 with triggers), pymupdf (page crops), pandoc (`.docx`), the H4 tool registry / `Tracer`; React 19 + Zustand 5 + BlockNote (MPL core) + react-i18next + `@base-ui/react` + sonner; Vitest/RTL/MSW; pytest.

**Spec:** [SRS §III-10.5 H5b](../specs/2026-05-17-paperhub-srs.md) (FR-23, UC-12, acceptance I-8 #12 — the H5b.1 parts), issue #51. **Visual base:** [`designs/2026-11-16-plan-h5b/`](../designs/2026-11-16-plan-h5b/README.md), the prototype signed off 2026-11-16 — match its layout, states, copy and motion; the Notes page view in it is H5b.2 and out of scope here.

## Global Constraints

- **Overlay only:** `source.html`, `chunks` and the PDF stay byte-identical; marks render over them (HTML: DOM overlay in the iframe like `lib/htmlTranslation.ts`; PDF: a positioned layer on each `<Page>`). No render-path file changes — prove it in Task 28 (`source.html` + `chunks` diff on a workspace copy = empty).
- **Yellow is reserved for citations.** Highlight colours: keys `green` `blue` `pink` `purple`, default names **Key result, Method, Question, Disagree**, values from the prototype (light `#2f9e63 #3b7dd8 #d6457e #8b5cd6`, dark `#36a067 #4b86dc #d9578a #9670dc`), validated with the dataviz skill's `scripts/validate_palette.js` in both modes before shipping. Comments use a neutral underline + tint, never yellow.
- **The agent never overwrites or deletes the user's words:** on a user-authored block (or an AI block the user has since edited) its `update` / `delete` become a **suggestion**.
- **All note text the agent read in a turn is private material** for the H5a checkpoint (`PrivateItem(kind="note")`). Notes are wrapped as data in every prompt.
- **Every delete has Undo; Undo turns into "Delete again" / "Erase again" / "Apply again" / "Make a divider again".** Toasts use the shipped `sonner` toaster (`import { toast } from "sonner"`, `action: {label, onClick}`), never a custom toast.
- **New tables carry `owner_id INTEGER NOT NULL DEFAULT 1`; history rows an `actor TEXT` (`user` / `agent` / `import` / `system`).**
- **Editor library:** BlockNote core packages only (MIT / MPL-2.0). No `@blocknote/xl-*` (GPL / commercial) — `.docx` is made server-side by pandoc. Task 1 records the exact packages and versions.
- **Record principle:** `notes:list`, `notes:search`, `notes:read`, `notes:edit` steps record note ids, block ids returned / touched, revisions and op outcomes — never the prompt.
- **Prompts** (the core agent's notes rules and the four note tool descriptions) are authored by an agent that **invokes `writing-agent-prompts` first** (≥ 2 variants × query set × judged comparison on the deploy model).
- **Migration rule:** the `ideas.note` backfill and the re-anchor step are idempotent CLIs wired into `backend/scripts/reingest_all_papers.ps1` and **run on the live workspace**; the live gate opens a paper ingested before this branch and marks it.
- Every UI string through `t("notes:…", "English")` in en / zh-TW / zh-CN / ja (new namespace `notes`); every appear / disappear animates, exits included; `prefers-reduced-motion` respected. A title cut off by its width shows the full text on hover (`title` when `scrollWidth > clientWidth`).
- New env vars land in `backend/.env.example` (one-line style). Commits: Conventional Commits, one concern each, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Per task: only the relevant tests + targeted ruff / mypy / tsc; full suites at the end of each phase.

## Review Focus

1. **Re-ingest moves a marked passage** → the piece is re-found by its quoted text; when it is gone the block shows "anchor lost" with the quote kept, and a multi-piece mark shows "1 of 2 pieces lost". *(Task 6 `test_reanchor_after_offsets_move`, `test_reanchor_lost_keeps_quote`, `test_reanchor_partial_pieces`.)*
2. **The user edits a block while the agent's change set targets it** → that op is rejected with the block id, the other ops apply, the user's text is untouched. *(Task 3 `test_agent_op_on_block_changed_since_base_rejected`.)*
3. **The same note open in the pane and the note card (or two tabs)** → a user op on a block changed since its base revision comes back as a conflict and the client reloads that block; nothing is silently overwritten. *(Task 3 `test_user_conflict_returns_current_block`; Task 13 `editor reloads a conflicted block`.)*
4. **A chat with a chat note is deleted, restored, forked, purged** → soft-deleted with it, restored with it, copied on fork (paper notes shared, not copied), removed on purge; paper notes never touched. *(Task 5 `test_session_note_lifecycle`.)*
5. **pandoc is not installed** → Markdown export still works; Word export is greyed out with a one-line reason; no 500. *(Task 26 `test_docx_without_pandoc_409_reason`, `Export as Word disabled with reason`.)*

---

## Phase 0 — Editor decision

### Task 1: BlockNote verification spike

**Files:**
- Create (throwaway, not committed): `frontend/src/spike/BlockNoteSpike.tsx`
- Modify: `docs/superpowers/designs/2026-11-16-plan-h5b/README.md` (section "Editor library — verified")

**Interfaces:**
- Produces: the decision record — package list with versions and licences; UI flavour (`@blocknote/shadcn` preferred for Tailwind theming, `@blocknote/mantine` fallback); whether `@blocknote/code-block` (syntax highlighting) and BlockNote comments are in the MIT / MPL core; React 19 compatibility; that block ids survive `editor.document` round-trips (`JSON → replaceBlocks → JSON` keeps every `id`); the custom-block API used in Task 13.

- [ ] **Step 1:** Install the candidate packages on a scratch branch; read each package's `LICENSE` and `package.json` `license`. Any GPL / commercial package is rejected.
- [ ] **Step 2:** Render a spike editor in a Vitest jsdom test and in the dev server: custom block `paperQuote` (props `markId`, `color`, `quote`, `source`), a slash item, a `---` input rule, `__underline__` and `||hide||` input rules, ids stable across `replaceBlocks`, Tab / Shift+Tab nesting, code-block highlighting if available.
- [ ] **Step 3:** Write the README section: verified packages, licences, chosen fallbacks (a custom highlight.js code block if `@blocknote/code-block` is not MIT / MPL; a custom `comment` style mark + PaperHub thread store if comments are not in the open core).
- [ ] **Step 4: Commit** `docs(h5b): record the verified editor library` (README only; delete the spike).

## Phase 1 — Note store and API (backend)

### Task 2: Notes tables and the block store

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`, `backend/src/paperhub/db/migrate.py` (FTS rebuild guard like `chunks_fts`)
- Create: `backend/src/paperhub/notes/__init__.py`, `notes/models.py`, `notes/order.py`, `notes/store.py`
- Test: `backend/tests/notes/test_store.py`, `backend/tests/notes/test_order.py`, `backend/tests/db/test_notes_schema.py`

**Interfaces:**
- Produces (tables, all `IF NOT EXISTS`):
  - `notes(id INTEGER PK, owner_id INTEGER NOT NULL DEFAULT 1, target_kind TEXT NOT NULL CHECK (target_kind IN ('paper','session','idea')), target_id TEXT NOT NULL, session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE, title TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(owner_id, target_kind, target_id))` — `session_id` set only for `session` notes (purge cascades).
  - `note_blocks(id TEXT PK, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, parent_id TEXT, order_key TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', author TEXT NOT NULL CHECK (author IN ('user','ai')), section_key TEXT, changed_rev INTEGER NOT NULL, changed_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT)` + index `(note_id, parent_id, order_key)`.
  - `note_block_history(id INTEGER PK, note_id INTEGER NOT NULL, block_id TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL, op TEXT NOT NULL CHECK (op IN ('insert','update','delete','move','restore')), before_json TEXT, after_json TEXT, run_id INTEGER, created_at TEXT NOT NULL)`.
  - `note_revisions(note_id INTEGER NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL, summary TEXT NOT NULL, change_set_id TEXT, run_id INTEGER, created_at TEXT NOT NULL, PRIMARY KEY (note_id, revision))`.
  - `note_blocks_fts USING fts5(text, content='note_blocks', content_rowid='rowid')` + `_ai/_ad/_au` triggers (pattern of `chunks_fts`; deleted blocks leave the index).
- Produces (Python):
  - `notes/models.py`: `BlockType = Literal["paragraph","heading","bulletListItem","numberedListItem","checkListItem","quote","callout","codeBlock","math","divider","section","paperQuote","paperFigure","paperEquation","paperRegion"]`; `class Block(BaseModel): id: str; note_id: int; parent_id: str | None; type: BlockType; props: dict[str, Any]; content: list[dict[str, Any]] | str | None; text: str; author: Literal["user","ai"]; section_key: str | None; changed_rev: int`; `class Note(BaseModel): id: int; target_kind: str; target_id: str; title: str; revision: int; updated_at: str`.
  - `notes/order.py`: `key_between(a: str | None, b: str | None) -> str` (fractional index over base-62; `key_between(None, None) == "a0"`; always `a < k < b`).
  - `notes/store.py`: `new_block_id() -> str` (`"b_"` + 10 base-62 chars); `block_text(type, props, content) -> str` (inline text runs joined; `paperQuote` → its quote; `math` → its LaTeX; `codeBlock` → code); `async get_or_create_note(conn, *, target_kind, target_id, title, session_id=None) -> Note`; `async get_note(conn, note_id) -> Note | None`; `async list_blocks(conn, note_id, *, include_deleted=False) -> list[Block]` (document order: depth-first by `order_key`); `async bump_revision(conn, note_id, *, actor, summary, change_set_id=None, run_id=None) -> int`.

- [ ] **Step 1: Write the failing tests** — `test_key_between_orders` (100 random inserts keep order; `key_between("a0","a1")` strictly between); `test_get_or_create_is_idempotent`; `test_unique_per_target`; `test_list_blocks_document_order` (children follow their parent); `test_block_text_extracts_plain`; `test_fts_tracks_insert_update_delete`; `test_schema_applies_twice` (idempotent; the FTS guard fills an empty index).
- [ ] **Step 2: Run** `uv run pytest tests/notes tests/db/test_notes_schema.py -v` — FAIL.
- [ ] **Step 3: Implement** the tables, models, order keys and store helpers.
- [ ] **Step 4: Run** the tests + `tests/db` + ruff + `mypy src/paperhub/notes` — PASS.
- [ ] **Step 5: Commit** `feat(notes): add the notes tables and block store`.

### Task 3: The op engine — revisions, history, conflicts, suggestions, undo

**Files:**
- Create: `backend/src/paperhub/notes/ops.py`
- Modify: `backend/src/paperhub/db/schema.sql` (`note_change_sets`, `note_suggestions`)
- Test: `backend/tests/notes/test_ops.py`

**Interfaces:**
- Consumes: Task 2 store.
- Produces:
  - Tables: `note_change_sets(id TEXT PK, owner_id INTEGER NOT NULL DEFAULT 1, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, actor TEXT NOT NULL, run_id INTEGER, base_revision INTEGER NOT NULL, result_revision INTEGER NOT NULL, ops_json TEXT NOT NULL, outcome_json TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('applied','undone')), created_at TEXT NOT NULL)`; `note_suggestions(id TEXT PK, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, block_id TEXT NOT NULL, change_set_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('update','delete')), proposed_json TEXT, status TEXT NOT NULL CHECK (status IN ('open','accepted','rejected','withdrawn')), created_at TEXT NOT NULL)`.
  - `class BlockIn(BaseModel): type: BlockType; props: dict[str, Any] = {}; content: list[dict[str, Any]] | str | None = None`.
  - `class Op(BaseModel): op: Literal["insert","update","delete","move"]; block_id: str | None = None; block: BlockIn | None = None; parent_id: str | None = None; after: str | None = None; before: str | None = None; under_heading: str | None = None; divider_before: bool = False` — placement `after` / `before` a block, or the end of the section under `under_heading` (a `section` / `heading` block id); a client-generated insert `block_id` is kept when unused.
  - `class ApplyResult(BaseModel): note_id: int; revision: int; change_set_id: str; applied: list[str]; suggested: list[dict[str, str]]  # {block_id, suggestion_id}; rejected: list[dict[str, Any]]  # {block_id, reason: "changed_since_base"|"missing"|"invalid", current: Block | None}`.
  - `async apply_ops(conn, note_id: int, *, base_revision: int, ops: list[Op], actor: Literal["user","agent","import","system"], run_id: int | None = None, summary: str = "") -> ApplyResult` — one transaction, one revision bump per call (a user typing burst = one call).
  - Rules: an op on a block with `changed_rev > base_revision` and `changed_by != actor` → rejected `changed_since_base` with `current`; actor `agent` on a block whose `author == "user"` → a `note_suggestions` row, block unchanged; actor `user` updating an `ai` block → `author = "user"`; deletes are soft (`deleted_at`); every applied op writes `note_block_history`.
  - `async undo_change_set(conn, change_set_id) -> ApplyResult` — reverts applied ops in reverse, skips blocks changed after the set (returned in `rejected` as `changed_since_base` = "kept, you edited it"), withdraws its open suggestions, status `undone`; `async reapply_change_set(conn, change_set_id) -> ApplyResult` (Apply again); `async restore_blocks(conn, note_id, block_ids, *, actor) -> ApplyResult`; `async resolve_suggestion(conn, suggestion_id, accept: bool) -> ApplyResult`.

- [ ] **Step 1: Write the failing tests** — `test_insert_under_heading_lands_at_section_end`; `test_one_revision_per_apply`; `test_history_rows_per_op`; `test_agent_update_on_user_block_becomes_suggestion`; `test_agent_delete_on_user_block_becomes_suggestion`; `test_agent_op_on_block_changed_since_base_rejected` (other ops still apply); `test_user_conflict_returns_current_block`; `test_user_edit_makes_ai_block_users`; `test_undo_change_set_keeps_user_edited_blocks`; `test_reapply_after_undo`; `test_restore_deleted_blocks`; `test_accept_and_reject_suggestion`.
- [ ] **Step 2: Run** `uv run pytest tests/notes/test_ops.py -v` — FAIL.
- [ ] **Step 3: Implement** `ops.py` and the two tables.
- [ ] **Step 4: Run** — PASS; ruff + mypy.
- [ ] **Step 5: Commit** `feat(notes): add the op engine with suggestions and undo`.

### Task 4: Paper notes, section headings, parts and marks

**Files:**
- Create: `backend/src/paperhub/notes/papers.py`, `notes/marks.py`
- Modify: `backend/src/paperhub/db/schema.sql` (`note_marks`, `mark_colors`)
- Test: `backend/tests/notes/test_paper_notes.py`, `backend/tests/notes/test_marks.py`

**Interfaces:**
- Consumes: Tasks 2–3; `paper_content.sections_json` order.
- Produces:
  - Tables: `note_marks(id TEXT PK, owner_id INTEGER NOT NULL DEFAULT 1, paper_content_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE, block_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('highlight','quote','figure','equation','region')), color TEXT, view TEXT NOT NULL CHECK (view IN ('html','pdf')), pieces_json TEXT NOT NULL, lang TEXT, source_ref TEXT, section_key TEXT, status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','lost','partly_lost','translation_changed')), created_at TEXT NOT NULL, deleted_at TEXT)`; `mark_colors(owner_id INTEGER NOT NULL DEFAULT 1, color TEXT NOT NULL, name TEXT NOT NULL, visible INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (owner_id, color))` seeded green / blue / pink / purple → Key result / Method / Question / Disagree.
  - Piece shapes (`pieces_json` list; `quote` always present): HTML `{"quote", "chunk_start", "chunk_end", "prefix", "suffix"}` (prefix / suffix ≤ 32 chars); PDF text `{"quote", "page", "block_id", "rects": [[x0,y0,x1,y1], …]}` (page fractions, 0-based page); box `{"quote", "page", "box": [x0,y0,x1,y1], "ocr": bool}`; figure / equation `{"quote", "figure_id" | "block_id", "page", "box", "latex"?}`; translation `{"quote", "lang", "source_ref", "orig"}`.
  - `papers.py`: `async paper_note(conn, paper_content_id) -> Note` (title = paper title); `async ensure_section(conn, note_id, paper_content_id, section_key: str) -> str` (the `section` block id, inserted in the paper's section order when missing); `parts_of_section(blocks: list[Block], section_block_id: str) -> list[list[Block]]` (only `divider` splits; the heading belongs to the first part); `async destination(conn, note_id, section_block_id, hint_block_id: str | None) -> tuple[str | None, int, int]` → `(after_block_id, part_index, part_count)`: the hint's part when it sits under the same section, else the section's end.
  - `marks.py`: `class MarkIn(BaseModel): paper_content_id: int; kind: str; color: str | None; view: str; pieces: list[dict[str, Any]]; lang: str | None = None; source_ref: str | None = None; section_key: str | None; hint_block_id: str | None = None; join_to: str | None = None`; `class SavedTo(BaseModel): note_id: int; paper_title: str; section: str | None; part_index: int; part_count: int`; `async create_mark(conn, m: MarkIn) -> tuple[dict, SavedTo]` (creates the `paperQuote` / `paperFigure` / `paperEquation` / `paperRegion` block via `apply_ops`, actor `user`; `join_to` appends a piece to that mark and updates its block); `async list_marks(conn, paper_content_id) -> list[dict]` (with `block_id`, `note_id`, `part_label`); `async delete_mark(conn, mark_id) -> ApplyResult` (soft-deletes mark + its block; the user's text beside it stays); `async restore_mark(conn, mark_id) -> ApplyResult`; `async erase_piece(conn, mark_id, index) -> dict`; `async restore_piece(conn, mark_id, index) -> dict`; `async split_last_piece(conn, mark_id) -> dict`; `async colors(conn) -> list[dict]`; `async set_color(conn, color, *, name=None, visible=None) -> None`.

- [ ] **Step 1: Write the failing tests** — `test_section_heading_created_in_paper_order`; `test_parts_split_only_at_dividers` (`text / quote / text / --- / text` → 2 parts; a sub-heading stays inside its part); `test_destination_uses_hint_part_in_same_section`; `test_destination_falls_back_to_section_end`; `test_create_mark_builds_quote_block`; `test_join_appends_piece`; `test_split_last_piece`; `test_delete_and_restore_mark_keeps_user_text`; `test_erase_piece_keeps_rest`; `test_colors_seeded_and_renamed`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS; ruff + mypy.
- [ ] **Step 5: Commit** `feat(notes): add paper notes, section parts and marks`.

### Task 5: Notes and marks API; chat-note lifecycle; page crops

**Files:**
- Create: `backend/src/paperhub/api/notes.py`, `backend/src/paperhub/api/marks.py`
- Modify: `backend/src/paperhub/app.py` (mount), `backend/src/paperhub/api/sessions.py` (`delete_session` / restore soft-delete the session note), `backend/src/paperhub/db/fork.py` (copy the session note with new block ids; paper notes shared), `backend/src/paperhub/api/papers.py` (`page-crop`, `blocks`)
- Test: `backend/tests/api/test_notes_api.py`, `backend/tests/api/test_marks_api.py`, `backend/tests/notes/test_session_note_lifecycle.py`, `backend/tests/api/test_page_crop.py`

**Interfaces:**
- Consumes: Tasks 2–4; `read_block_assets`; pymupdf.
- Produces:
  - `GET /notes/target?kind=paper|session|idea&id=` → `{note, blocks}` (creates the note); `GET /notes/{id}` → `{note, blocks, suggestions, comments}`; `POST /notes/{id}/ops` `{base_revision, ops}` → `ApplyResult` (actor `user`; conflicts are in `rejected`, never a 409); `POST /notes/{id}/restore` `{block_ids}`; `POST /notes/suggestions/{id}` `{accept}`; `POST /notes/change-sets/{id}/undo` and `/reapply` → `ApplyResult`.
  - `POST /marks` (MarkIn) → `{mark, block, saved_to}`; `GET /marks?paper_content_id=`; `DELETE /marks/{id}` + `POST /marks/{id}/restore`; `DELETE /marks/{id}/pieces/{i}` + `POST /marks/{id}/pieces/{i}/restore`; `POST /marks/{id}/split`; `GET /marks/colors`, `PATCH /marks/colors/{color}` `{name?, visible?}`.
  - `GET /papers/content/{id}/page-crop?page=&x0=&y0=&x1=&y1=` → PNG (fractions, 0-based page, pymupdf at 2×, longest side ≤ 1600 px); `GET /papers/content/{id}/blocks?page=` → `[{block_id, type, page, box (fractions), text}]` from `blocks.json`.
  - Chat-note lifecycle: deleting a session sets the session note's `deleted_at`, restore clears it, purge cascades; fork creates the forked session's note as a copy (new block ids, `author` kept, one revision `actor=system summary="forked"`).

- [ ] **Step 1: Write the failing tests** — `test_target_endpoint_creates_once`; `test_ops_endpoint_returns_apply_result`; `test_marks_roundtrip_and_saved_to`; `test_mark_delete_restore_via_api`; `test_colors_patch`; `test_session_note_lifecycle`; `test_page_crop_png_bounds`; `test_blocks_endpoint_fractions`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/api/test_sessions*.py tests/db/test_fork*.py` — PASS.
- [ ] **Step 5: Commit** `feat(notes): add the notes and marks API`.

### Task 6: Anchors survive re-ingest and section renames

**Files:**
- Create: `backend/src/paperhub/notes/anchors.py`, `backend/src/paperhub/cli/reanchor_marks.py`
- Modify: `backend/src/paperhub/db/section_rename.py` (`apply_section_renames` renames `note_blocks.section_key` + the section heading text and `note_marks.section_key`; `RenameReport.note_sections`), `backend/src/paperhub/cli/reingest.py` (call `reanchor_paper` after re-chunk), `backend/pyproject.toml` (`paperhub-reanchor-marks`), `backend/scripts/reingest_all_papers.ps1` (new numbered step after re-chunk; renumber the labels)
- Test: `backend/tests/notes/test_anchors.py`, `backend/tests/db/test_section_rename.py` (extend)

**Interfaces:**
- Produces: `async reanchor_paper(conn, paper_content_id) -> dict[str, int]` (`{"ok", "moved", "lost", "partly_lost"}`) — per HTML piece: find the quote in the paper's chunk text (exact, then whitespace-normalised, then prefix + suffix to pick among repeats) → new `chunk_start` / `chunk_end`; not found → piece lost; all lost → mark `lost` (block prop `anchorLost: true`, quote kept); some → `partly_lost`. PDF pieces keep their geometry. CLI `paperhub-reanchor-marks [--dry-run]` runs it for every paper with marks.

- [ ] **Step 1: Write the failing tests** — `test_reanchor_after_offsets_move`; `test_reanchor_lost_keeps_quote`; `test_reanchor_partial_pieces`; `test_section_rename_moves_note_heading`; `test_dropped_section_heading_marked_lost`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/cli/test_reingest*.py` — PASS.
- [ ] **Step 5: Commit** `feat(notes): re-anchor marks after re-ingest and section renames`.

### Task 7: Idea notes and the `ideas.note` migration

**Files:**
- Create: `backend/src/paperhub/cli/migrate_idea_notes.py`
- Modify: `backend/pyproject.toml` (`paperhub-migrate-idea-notes`), `backend/scripts/reingest_all_papers.ps1` (new numbered step), `backend/src/paperhub/ideation/agent.py` (`_rejected()` reads the idea note's text), `backend/src/paperhub/api/ideation.py` (`Idea.note` filled from the idea note; `PATCH note` writes the idea note — kept for one release so an old client works)
- Test: `backend/tests/cli/test_migrate_idea_notes.py`, `backend/tests/ideation/test_agent_reads_idea_note.py`

**Interfaces:**
- Produces: `async idea_note_text(conn, idea_id: int) -> str` (block text joined by blank lines); `async migrate(db_path, *, dry_run) -> list[dict]` — each idea with a non-empty `note` and no idea note yet gets one (`target_kind='idea'`), one `paragraph` block per non-empty paragraph, actor `system`, summary `"moved from ideas.note"`; a second run reports `skipped_exists`; the `ideas.note` column stays.

- [ ] **Step 1: Write the failing tests** — `test_migration_moves_every_note` (joined text equal); `test_migration_idempotent`; `test_dry_run_writes_nothing`; `test_rejected_prompt_uses_idea_note`; `test_patch_note_writes_idea_note`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/ideation` — PASS.
- [ ] **Step 5: Commit** `feat(notes): move idea notes into the notes store`.

## Phase 2 — The agent and notes

### Task 8: Read tools — `list_notes`, `search_notes`, `read_note`

**Files:**
- Create: `backend/src/paperhub/agents/core/note_tools.py`, `backend/src/paperhub/notes/scope.py`
- Modify: `backend/src/paperhub/agents/core/action_tools.py` (`build_default_registry` calls `register_note_tools`), `backend/src/paperhub/outbound/models.py` (`PrivateKind` += `"note"`), `backend/src/paperhub/agents/core/private_material.py` (`collect_note_blocks`), `backend/src/paperhub/db/schema.sql` (`note_agent_reads`)
- Test: `backend/tests/agents_core/test_note_read_tools.py`

**Interfaces:**
- Produces:
  - `note_agent_reads(session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, revision INTEGER NOT NULL, at TEXT NOT NULL, PRIMARY KEY (session_id, note_id))`.
  - `scope.py`: `async notes_in_scope(conn, session_id) -> list[Note]` (the session note, the notes of the chat's enabled papers, the idea note in an idea chat); `async note_ref(conn, ref: str, session_id) -> Note` — accepts `"chat"`, `"paper:<paper_content_id>"`, `"idea:<id>"`, `"note:<id>"` (paper / chat / idea notes created on demand).
  - Tools (Tier `FREE`): `list_notes(scope: "chat"|"all" = "chat")` → per note `{ref, title, target, revision, blocks, outline: [{block_id, text, level}], last_editor, changed_since_you_read}`; `search_notes(query, scope="chat")` → ≤ 20 `{note, block_id, heading_path, snippet}` (FTS `bm25`); `read_note(note, from=None, page=1)` → `{note, revision, blocks: [{id, type, author, text}], next_page}` — a page is 40 blocks or 6 000 chars. Every returned block feeds `collect_note_blocks` (`PrivateItem(kind="note", ref=f"note_block:{id}", title=<note title>, text=<block text>)`) and `note_agent_reads`. Note text in results is wrapped as `<note ref=… title=…><note_block id=… author=…>…</note_block></note>`. Steps `notes:list|search|read` record note ids, block ids returned and revisions.

- [ ] **Step 1: Write the failing tests** — `test_list_notes_scope_chat`; `test_outline_and_changed_flag`; `test_search_returns_heading_path`; `test_read_note_pages_and_next_page`; `test_read_from_heading`; `test_read_adds_private_material`; `test_reads_recorded_in_trace`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (tool descriptions drafted through `writing-agent-prompts`; final wording in Task 11). **Step 4: Run** + `tests/agents_core` — PASS.
- [ ] **Step 5: Commit** `feat(agent): add note read tools`.

### Task 9: `edit_note`, the change card and its undo

**Files:**
- Modify: `backend/src/paperhub/agents/core/note_tools.py`, `backend/src/paperhub/agents/core/agent.py` (`REPLAYED_UI_EVENTS` += `notes_changed`), `backend/src/paperhub/api/chat.py` (`_SIDE_EFFECT_EVENTS`, `_side_effects_from_trace`)
- Test: `backend/tests/agents_core/test_edit_note_tool.py`

**Interfaces:**
- Consumes: `apply_ops`, `undo_change_set`, `reapply_change_set`, `note_ref`, `ensure_section`.
- Produces: tool `edit_note(note: str, base_revision: int, ops: list[Op])`, Tier `SHOW_UNDO`, actor `agent`; every `insert` must name `after` / `before` / `under_heading` (otherwise `invalid`); on a paper note `under_heading` may be a section name, resolved by `ensure_section`. Result to the model: `{revision, applied, suggested, rejected}` (rejected blocks with their current text). Event `("notes_changed", {change_set_id, note_id, note_title, summary: {added, updated, deleted, suggested, rejected, section}, revision})`. Step `notes:edit` records `{note_id, base_revision, revision, ops, applied, suggested, rejected}`.

- [ ] **Step 1: Write the failing tests** — `test_edit_inserts_under_section_name`; `test_insert_without_place_invalid`; `test_user_block_update_is_suggestion`; `test_event_summary_counts`; `test_card_replayed_after_reload`; `test_trace_records_outcome`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/agents_core tests/api/test_chat*.py` — PASS.
- [ ] **Step 5: Commit** `feat(agent): let the agent edit notes as an undoable change set`.

### Task 10: `<notes>` and `<notes_changes>` in session state; `[note:B]` citations

**Files:**
- Modify: `backend/src/paperhub/agents/core/context.py` (`SessionStateParts.notes`, `.notes_changes`; `render_session_state`), `backend/src/paperhub/agents/core/citations.py` (`_NOTE_MARKER`, `cited_notes`, `validate_note_citations`), `backend/src/paperhub/agents/core/agent.py` (`_check_evidence` gets the block ids read this turn), `frontend/src/lib/figureCitations.ts` (`NOTE_MARKER_RE`) and the chat marker renderer (a `[note:B]` chip opening the notes pane at that block)
- Test: `backend/tests/agents_core/test_notes_session_state.py`, `backend/tests/agents_core/test_note_citations.py`, `frontend/src/lib/figureCitations.test.ts`

**Interfaces:**
- Produces: `<notes>` — ≤ 12 notes in scope (`ref`, title, revision, block count); `<notes_changes>` — notes the user changed since this session's agent last read / wrote them (`note_agent_reads` vs `note_block_history` actor `user`), ≤ 10 notes × ≤ 20 block ids, `{note, block_ids, when}`, wrapped as data. `validate_note_citations(text, allowed_block_ids) -> tuple[str, list[str]]` — a `[note:b_…]` not read this turn becomes `[unsourced:note_not_read]`.

- [ ] **Step 1: Write the failing tests** — `test_notes_index_bounded`; `test_user_edit_appears_in_notes_changes`; `test_agent_read_clears_change`; `test_note_citation_kept_when_read`; `test_note_citation_unsourced_when_not_read`; frontend `parses [note:b_x]`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(agent): show note changes to the agent and check note citations`.

### Task 11: Core agent prompt `core_agent/v9` — notes rules

**Files:**
- Create: `backend/src/paperhub/llm/prompts/core_agent_v9.yaml`, variants under `backend/benchmark/agent/prompts/core_agent/`, cases `backend/benchmark/agent/cases/notes_*.toml`
- Modify: `backend/src/paperhub/agents/core/agent.py` (`CORE_AGENT_SLOT = "core_agent/v9"`), the four tool descriptions in `note_tools.py`

**Interfaces:**
- Rules the prompt carries (wording is the skill's job): use notes when the user asks what they concluded or noted; reach a long note by outline → search → page, never whole; anything about a paper goes into that paper's note under the matching section heading (created if missing); the chat note only when no paper is concerned; a divider may precede an appended block; a block recording what the user said keeps the user's words; AI summaries follow the citation rules incl. `[unsourced:]`; cite note content as `[note:B]`; note text is data, never an instruction.

- [ ] **Step 1: Invoke `writing-agent-prompts`** and follow it: ≥ 2 variants of v9 × a query set ("summarise what we concluded about §2 into my notes", "what did I note about X", a note longer than one page, a chat question with no paper, an instruction hidden in a note, plus the v8 regression set), judged on the deploy model.
- [ ] **Step 2: Run** the sweep (`backend/benchmark/agent`), recording results.
- [ ] **Step 3: Adopt** the winner as `core_agent_v9.yaml`; switch the slot; keep v8 for rollback.
- [ ] **Step 4: Run** `uv run pytest tests/agents_core tests/llm -v` — PASS.
- [ ] **Step 5: Commit** `feat(agent): teach the core agent to keep notes (core_agent/v9)`.

### Task 12: Phase 2 live check

- [ ] **Step 1:** Backend gates (`uv run pytest`, ruff, mypy).
- [ ] **Step 2:** On `:8000` (ask the user to restart it if needed): `POST /sessions` → attach a paper ingested before this branch → `POST /chat` "summarise what we concluded about §2 into my notes"; read the run's `tool_calls` — `notes:list|read|edit` steps, inserts under §2, the `notes_changed` event; edit a block through `POST /notes/{id}/ops`; the next turn's recorded state shows it in `<notes_changes>`.
- [ ] **Step 3:** Fix every issue found before Phase 3.

## Phase 3 — Notes pane and editor (frontend)

### Task 13: Notes store, API client, BlockNote editor with PaperHub blocks

**Files:**
- Modify: `frontend/package.json` (Task 1's packages), `frontend/src/lib/api.ts`, `frontend/src/lib/i18n.ts` + `src/locales/{en,zh-TW,zh-CN,ja}/notes.json`
- Create: `frontend/src/types/notes.ts`, `frontend/src/store/notes.ts`, `frontend/src/components/notes/NoteEditor.tsx`, `components/notes/schema.ts`, `components/notes/blocks/{SectionBlock,PaperQuoteBlock,PaperFigureBlock,PaperEquationBlock,PaperRegionBlock}.tsx`, `components/notes/sync.ts`
- Test: `frontend/src/components/notes/sync.test.ts`, `NoteEditor.test.tsx`, `frontend/src/store/notes.test.ts`

**Interfaces:**
- Produces:
  - `api.ts`: `getNoteForTarget(kind, id)`, `getNote(id)`, `postNoteOps(id, body)`, `restoreNoteBlocks(id, blockIds)`, `resolveSuggestion(id, accept)`, `undoChangeSet(id)`, `reapplyChangeSet(id)`, `createMark(body)`, `listMarks(paperContentId)`, `deleteMark(id)`, `restoreMark(id)`, `erasePiece(id, i)`, `restorePiece(id, i)`, `splitMark(id)`, `listMarkColors()`, `patchMarkColor(color, body)`, `pageCropUrl(paperContentId, page, box)`, `listPaperBlocks(paperContentId, page)`.
  - `useNotesStore`: `notes: Record<number, NoteState>` (`{note, blocks, suggestions, comments, revision}`), `loadTarget(kind, id)`, `applyLocal(noteId, ops)`, `flush(noteId)` (debounced 600 ms, one `postNoteOps` per burst), `onConflict(noteId, rejected)` (replaces those blocks; toast "Updated from another view"), `activePart: Record<number, string | null>`, `focusOn`, `followOn` (persisted), `folded: Record<number, string[]>` (persisted).
  - `sync.ts`: `diffDocuments(prev: Block[], next: Block[]) -> Op[]` (by stable block id: insert / update / delete / move with `after` / `parent_id`).
  - `schema.ts`: BlockNote defaults + `section` (read-only heading, section key), `paperQuote` / `paperFigure` / `paperEquation` / `paperRegion` (non-editable content; paper serif, muted, a bar in the mark colour; tags "From the paper · <colour name> · §N · p. N" and "文 Translated · 繁中" with Show original; "anchor lost" and "1 of 2 pieces lost" states; source chip), `divider` if BlockNote lacks one.
  - `NoteEditor({ noteId, variant: "pane" | "card", onBlockFocus? })`.

- [ ] **Step 1: Write the failing tests** — `diffDocuments` (insert / update / delete / move / nested parent); the store batches a burst into one call (MSW); `editor reloads a conflicted block`; `NoteEditor` renders a `paperQuote` with its tag and colour bar and a `section` heading.
- [ ] **Step 2: Run** `npm test -- --run src/components/notes src/store/notes.test.ts` — FAIL.
- [ ] **Step 3: Implement.** The Notion behaviours BlockNote already has (Enter / Backspace / arrows / `/` menu / `+` / `⋮⋮` / formatting bar / Markdown shortcuts / Tab nesting / undo) are configured, not rewritten; PaperHub `/` items: Quote from the paper, Link a paper or section (`@` chip); Enter on a selected reference / section / divider adds a line below.
- [ ] **Step 4: Run** — PASS; `npm run typecheck`, `npm run lint`.
- [ ] **Step 5: Commit** `feat(notes): add the notes store and the block editor`.

### Task 14: Right panel — Notes pane, Reader | Notes tabs, side by side

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`, `frontend/src/components/chat/Composer.tsx` (Notes button `NotebookPen`, `aria-pressed`), `frontend/src/store/canvas.ts`, `frontend/src/hooks/useCanvasResize.ts`
- Create: `frontend/src/store/notesPane.ts`, `frontend/src/components/notes/NotesPane.tsx`, `components/notes/NotesTabs.tsx`
- Test: `frontend/src/pages/ChatPage.notes.test.tsx`, `frontend/src/store/notesPane.test.ts`

**Interfaces:**
- Produces: `useNotesPaneStore` (persisted `paperhub-notes-pane-v1`): `open`, `activeTab: "reader"|"notes"`, `notesWidth` (default 420, min 320), `toggle()`, `setActiveTab()`, `setNotesWidth()`. Allowed pairs: Reader + Notes, Slides + Notes; Memory stays alone (opening it closes Notes and the reverse). Both open and panel width ≥ `SIDE_BY_SIDE_MIN = 760` → side by side (each edge resizes only the pane beside it; Notes keeps its width); else tabs. Opening a pane never resets the dragged width. Note tabs: the paper (`Notes on: <title>` tooltip) and "This chat" (`Notes for this chat only: <chat title>`).

- [ ] **Step 1: Write the failing tests** — the Notes button opens the pane; tabs below 760 px, side by side above; Memory closes Notes; widths remembered; truncated titles get a `title` (reader paper tabs, the note tabs, the sidebar chat list — one shared `useTruncatedTitle` helper in `src/lib/`).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (tabs slide in their direction; panes open / close by `transform`). **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notes): add the Notes pane beside the reader`.

### Task 15: Inline marks, code highlighting, formatting bar extras, shortcuts guide

**Files:**
- Create: `frontend/src/components/notes/inlineMarks.ts`, `components/notes/ShortcutsGuide.tsx`, `components/notes/EditorTip.tsx`
- Modify: `components/notes/schema.ts` (styles `underline` if missing, `hidden`; code block per Task 1), `NoteEditor.tsx` (formatting bar: B I U S · quote, code, hide · Link, Comment; tooltips name the Markdown and key)
- Test: `frontend/src/components/notes/inlineMarks.test.ts`, `ShortcutsGuide.test.tsx`

**Interfaces:**
- Produces: `INLINE_RULES` — `**x**` bold, `*x*` / `_x_` italic (never inside `snake_case`), `__x__` underline, `~~x~~` strike, `` `x` `` code, `||x||` hidden (click to show), firing on the closing mark. Keys: Ctrl+E code, Ctrl+Shift+S strike, Ctrl+Shift+M comment. Code block: ```` ```lang ```` + Enter / space; Enter newline, Tab indent, Ctrl+Enter leaves; editable language label; highlighting follows it (plain / unknown uncoloured); token colours per theme. `EditorTip` shows once (localStorage `paperhub-notes-tip-v1`, wrapped in try/catch). `ShortcutsGuide` sections: Marks as you type (rendered), At the start of a line, Anywhere, Lists, Moving around, Keys, The notes pane, On the paper.

- [ ] **Step 1: Write the failing tests** — each rule converts on its closing mark; `my_var_name` and `**b*` untouched; the guide lists every section; tip dismissal remembered.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notes): add inline marks, code highlighting and the shortcuts guide`.

### Task 16: Comments

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`, `backend/src/paperhub/api/notes.py` (comment routes), `backend/src/paperhub/agents/core/note_tools.py` (`read_note` returns open comments as `<user_comment>` context; the agent never writes or resolves them)
- Create: `frontend/src/components/notes/CommentThread.tsx`
- Test: `backend/tests/api/test_note_comments.py`, `frontend/src/components/notes/CommentThread.test.tsx`

**Interfaces:**
- Produces: `note_comments(id TEXT PK, owner_id INTEGER NOT NULL DEFAULT 1, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, block_id TEXT NOT NULL, quote TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('open','resolved','deleted')), created_at TEXT NOT NULL)`; `note_comment_items(id INTEGER PK, comment_id TEXT NOT NULL REFERENCES note_comments(id) ON DELETE CASCADE, owner_id INTEGER NOT NULL DEFAULT 1, text TEXT NOT NULL, created_at TEXT NOT NULL)`. Routes `POST /notes/{id}/comments` `{block_id, quote, text}`, `POST /notes/comments/{cid}/items` `{text}`, `PATCH /notes/comments/{cid}` `{status}`. The anchor is an inline `comment` style carrying the thread id; thread text is not note text (not in FTS, not exported).

- [ ] **Step 1: Write the failing tests** — create / reply / resolve / delete / reopen; comments excluded from `block_text` and FTS; the agent sees open comments labelled as the user's; UI: Enter posts, Shift+Enter newline, Esc on an empty new comment drops it, Resolve → Undo → "Resolve again".
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notes): add comments on note text`.

### Task 17: Dividers as notes, Focus, folding, Follow, the notes toolbar

**Files:**
- Create: `frontend/src/components/notes/parts.ts`, `components/notes/NotesToolbar.tsx`
- Modify: `NoteEditor.tsx` (divider with the fold chevron at the content edge, darkened dividers around the active part, `data-fade`), `NotesPane.tsx`
- Test: `frontend/src/components/notes/parts.test.ts`, `NotesToolbar.test.tsx`

**Interfaces:**
- Produces: `parts.ts`: `partsOfSection(blocks, sectionId): Part[]` (same rule as the backend), `activePartFor(blockId): string | null`, `marksInPart(part): string[]`. Toolbar: left **Follow** ("Following §2" / "Follow reading"); right **Focus** (label hidden under a 380 px container) and **⋯** ("Shared by N chats · revision R", Export as Markdown, Export as Word, Shortcuts). Typing `---` → divider at once; toast Undo → `---` → "Make a divider again". The divider chevron folds the part below to "N blocks · M marks"; fold state persisted per note.

- [ ] **Step 1: Write the failing tests** — parts split only at dividers; Focus fades the other parts and exposes the focused part's mark ids; Follow off stops scrolling with the reader; `---` → divider → Undo → `---`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notes): split notes with dividers; add Focus, folding and Follow`.

### Task 18: Suggestions and the agent's change card

**Files:**
- Create: `frontend/src/components/chat/NotesChangeCard.tsx`, `frontend/src/components/notes/SuggestionMark.tsx`
- Modify: `frontend/src/hooks/useChatStream.ts` (`AGENT_EVENTS` += `notes_changed`), `frontend/src/types/domain.ts` (`NotesChangedData`), `frontend/src/components/chat/MessageBubble.tsx`, `NoteEditor.tsx` (inline tracked change with Accept / Reject)
- Test: `frontend/src/components/chat/NotesChangeCard.test.tsx`, `frontend/src/components/notes/SuggestionMark.test.tsx`

**Interfaces:**
- Produces: a one-line card ("Added 3 notes under §2 · 1 suggestion on your text") with **Undo** (→ "Apply again") and **Open in notes**; Details folds out the diff (height transition). A `notes_changed` event reloads that note in the store.

- [ ] **Step 1: Write the failing tests** — the summary line from counts; Undo → Apply again → Undo; Open in notes opens the pane at the note; accept / reject calls the API and updates the block.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notes): show the agent's note changes as an undoable card`.

### Task 19: Idea notes in the Ideas page

**Files:**
- Modify: `frontend/src/components/ideas/IdeaDetail.tsx` (the note `<Textarea>` becomes `<NoteEditor variant="pane">` on the idea's note), `IdeaDrawer.tsx`, `IdeaKanban.tsx` (read the note's first lines)
- Test: `frontend/src/components/ideas/IdeaDetail.test.tsx` (update)

- [ ] **Step 1: Update the tests** — the idea note opens in the editor; edits post ops; the drawer shows the note's first lines.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(ideas): edit an idea's note in the notes editor`.

### Task 20: Phase 3 live check

- [ ] Frontend gates (`npm test -- --run`, typecheck, lint, build). On the dev server against `:8000`: open an old paper's note; type with every shortcut, nest lists, add a code block, comment, split with `---`, Focus / fold / Follow; accept a suggestion from Task 12's run. Screenshot the pane in both themes; fix everything found before Phase 4.

## Phase 4 — Marks on the paper

### Task 21: HTML marks — popover, colours, overlay, destination toast

**Files:**
- Create: `frontend/src/lib/marks/anchor.ts`, `lib/marks/htmlMarks.ts`, `components/canvas/MarkPopover.tsx`, `store/marks.ts`
- Modify: `frontend/src/components/canvas/HtmlView.tsx` (attach marks like translations), `CitationCanvas.tsx`
- Test: `frontend/src/lib/marks/anchor.test.ts`, `lib/marks/htmlMarks.test.ts`, `components/canvas/MarkPopover.test.tsx`

**Interfaces:**
- Produces: `selectionToPieces(doc, range): Piece[]` (one piece per paragraph — a stroke never crosses a paragraph break; chunk range from the nearest `phchunk-N` spans; 32-char prefix / suffix); `attachHtmlMarks(doc, { marks, colors, focusIds, onClick, onHover }): HtmlMarksHandle` (`update`, `flash(markId)`, `detach`) — highlight = palette background, quote = thin neutral underline, note-only = margin icon with a count; hidden colours not drawn; a "Show my marks" toggle hides all. `useMarksStore`: `marksByPaper`, `colors`, `create(MarkIn)`, `remove(id)` (toast Undo ⇄ Delete again), `focusIds`. `MarkPopover` sits **below** the selection: four named colours, Quote to note, Add note; after saving, a toast "Saved to <paper> › §2 · note 2 of 3".

- [ ] **Step 1: Write the failing tests** — a selection across two paragraphs → two pieces; an anchor round-trip re-finds the range; the overlay draws, recolours on rename / hide, and leaves the document unchanged once overlay elements are removed; the popover sits below the selection; the toast text comes from `saved_to`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** Validate the palette with the dataviz validator in both modes; paste the output in the commit body. **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(reader): highlight and quote passages into notes`.

### Task 22: PDF marks — text-layer marks, Box tool, scanned pages, figure / equation marks

**Files:**
- Create: `frontend/src/components/canvas/PdfMarksLayer.tsx`, `components/canvas/BoxTool.tsx`, `components/canvas/ObjectMarkButton.tsx`
- Modify: `PdfView.tsx` (a layer per `<Page>`), `HtmlView.tsx` / `lib/marks/htmlMarks.ts` (mark button on `phfig-*` figures and rendered display equations), `CitationCanvas.tsx` (Box button in the reader bar, PDF only; Esc stops)
- Test: `PdfMarksLayer.test.tsx`, `BoxTool.test.tsx`, `ObjectMarkButton.test.tsx`

**Interfaces:**
- Consumes: `listPaperBlocks`, `pageCropUrl`.
- Produces: text marks store line rects as page fractions and the Marker `block_id` the selection falls in (its `section` → the note section); marks are hit-tested so text under them stays selectable. A drag with Box on, or on a page with no text layer, draws a box: its text = text-layer spans under it (Marker OCR blocks on a scanned page, `ocr: true`); a box covering > 50 % of a Marker `Equation` block snaps to it → an equation mark (LaTeX + crop, "transcribed by the model"). Hovering a figure / equation shows the corner mark button (colour, Add to note, Add note); a box inside a figure marks one panel.

- [ ] **Step 1: Write the failing tests** — rect fractions from a fake text layer; the hit-test passes events through; box text from the spans under it; equation snap above 50 %; a scanned-page box is `ocr: true`; a one-click figure mark creates a `figure` mark.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(reader): mark PDF pages, figures, equations and boxes`.

### Task 23: Highlighter mode

**Files:**
- Create: `frontend/src/lib/marks/pieces.ts`, `components/canvas/HighlighterControl.tsx`
- Modify: `CitationCanvas.tsx` (reader bar), `lib/marks/htmlMarks.ts`, `PdfMarksLayer.tsx`
- Test: `frontend/src/lib/marks/pieces.test.ts`, `HighlighterControl.test.tsx`

**Interfaces:**
- Produces: `joinTarget(marks, stroke): string | null` (same colour, same paragraph / Marker block, made while the pen is on → that mark); a stroke → `create({..., join_to})` and a toast "Joined to the same mark · Split" (`splitMark`); a pen click on a mark erases that piece (toast Undo ⇄ Erase again); a click on a figure / equation marks it whole; a box on a scanned page marks in the pen colour. The label names the colour; Esc or the button turns it off.

- [ ] **Step 1: Write the failing tests** — join rules; Split; erasing keeps the other pieces; pen off restores the popover.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(reader): add the highlighter mode`.

### Task 24: Marks on translations

**Files:**
- Modify: `frontend/src/lib/htmlTranslation.ts` (a selection inside `.ph-tr` → a translation piece), `components/canvas/TranslationCard.tsx` (selection in the card), `lib/marks/htmlMarks.ts` + `PdfMarksLayer.tsx` (文 marker), `store/translation.ts` (`showMark(markId)` turns translation on / switches language and returns the previous state)
- Test: `frontend/src/lib/marks/translationMarks.test.ts`, `TranslatePill.test.tsx` (extend)

**Interfaces:**
- Produces: a translation piece `{quote, lang, source_ref, orig}`; with its words on screen it is highlighted in the translation; otherwise a 文 marker in the mark colour sits on the paragraph / block. Asking to see it (source chip, Show ↗ jumper, Open in the reader) turns translation on (or to its language), scrolls, flashes and toasts "Translation turned on (繁中) to show this mark" with Turn off / Back to <lang>. The note block shows "文 Translated · 繁中" + Show original.

- [ ] **Step 1: Write the failing tests** — marking inside `.ph-tr` records `lang` + `source_ref`; translation off → marker, no highlight; `showMark` switches and toasts with restore.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(reader): mark translated text`.

### Task 25: The note card, source chips, back-to-note, My marks

**Files:**
- Create: `frontend/src/components/canvas/NoteCard.tsx`, `components/canvas/BackToNotePill.tsx`, `components/canvas/MyMarksMenu.tsx`
- Modify: `CitationCanvas.tsx`, `components/notes/blocks/PaperQuoteBlock.tsx` (source chip → reader), `lib/cardPlacement.ts` (stack with the translation card)
- Test: `NoteCard.test.tsx`, `MyMarksMenu.test.tsx`, `BackToNotePill.test.tsx`

**Interfaces:**
- Produces: `NoteCard` (pane hidden; click on a mark), placed with `placeCard` in the gutter left of the reader, arrow at the paragraph, always on screen, stacked above the translation card; title "<paper> › §2 · note 2 of 3"; the current part editable (`NoteEditor variant="card"`); other parts folded to "Note N · K blocks · <first words>" with their chevrons centred on the reference bar; only the clicked reference tinted; other references are **Show ↗** jumpers; trash = delete the mark (Undo ⇄ Delete again). With the pane visible, hovering a mark tints its block and darkens the part's dividers. Source chip → reader at the mark + flash + "↩ back to note" pill (fades once the user scrolls away). `MyMarksMenu`: grouped by section in reading order, colour names editable, show / hide per colour, counts, click → scroll + flash.

- [ ] **Step 1: Write the failing tests** — card title, folded parts, only the clicked reference tinted, a jumper re-points the card; delete → Undo → Delete again; a source chip opens the reader and shows the pill; My marks rename + hide + jump.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(reader): add the note card, source chips and My marks`.

## Phase 5 — Export, privacy, verification

### Task 26: Export as Markdown and Word

**Files:**
- Create: `backend/src/paperhub/notes/export.py`, `backend/src/paperhub/notes/pandoc.py`
- Modify: `backend/src/paperhub/api/notes.py` (`GET /notes/{id}/export?format=md|docx`, `GET /notes/export/capabilities`), `backend/src/paperhub/db/schema.sql` (`note_exports(id INTEGER PK, owner_id INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL, note_id INTEGER NOT NULL, format TEXT NOT NULL, created_at TEXT NOT NULL)`), `frontend/src/components/notes/NotesToolbar.tsx` (⋯ items; Word disabled with a reason)
- Test: `backend/tests/notes/test_export.py`, `backend/tests/api/test_notes_export.py`, `frontend/src/components/notes/NotesToolbar.test.tsx` (extend)

**Interfaces:**
- Produces: `note_to_markdown(note, blocks, papers) -> tuple[str, dict[str, bytes], str]` (Markdown, image files, BibTeX) — sections as `##`; quotes as `>` with an "— <paper>, §<section>, p. <page>" line; citations as `[@key]` with entries in `references.bib` (key `<firstauthor><year><firstword>`); figures, equations and boxes as image files (figure image or `page-crop`) with captions; math as `$…$` / `$$…$$`; dividers `---`; hidden text and comments as plain text / omitted. `pandoc_available() -> bool` (`shutil.which`); `markdown_to_docx(md, resources: dict[str, bytes]) -> bytes` (pandoc in a temp dir, 60 s timeout, modelled on `table_figures._render_pandoc`). `md` → `application/zip` `<slug>-notes.zip` (`note.md`, `images/…`, `references.bib`); `docx` without pandoc → 409 `{"detail": "pandoc_missing"}`. Each export writes a `note_exports` row.

- [ ] **Step 1: Write the failing tests** — `test_markdown_has_sections_quotes_and_sources`; `test_bib_has_one_entry_per_quoted_paper`; `test_images_written_for_figures_and_boxes`; `test_divider_exports_as_rule`; `test_comments_not_exported`; `test_docx_roundtrip_when_pandoc_present` (skipped without pandoc); `test_docx_without_pandoc_409_reason`; `test_export_recorded`; frontend `Export as Word disabled with reason`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notes): export a note as Markdown or Word`.

### Task 27: Notes in the outbound checkpoint

**Files:**
- Modify: `backend/src/paperhub/outbound/checkpoint.py` (span guard covers `note` items), `backend/src/paperhub/agents/core/private_material.py`
- Test: `backend/tests/outbound/test_note_private_material.py`

- [ ] **Step 1: Write the failing tests** — a search query repeating an 8-word window of a note block read this turn → the unsure policy, stage `span`; a note not read this turn adds nothing; AI-written and quoted note text count too.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/outbound` — PASS.
- [ ] **Step 5: Commit** `feat(outbound): treat note text read in a turn as private material`.

### Task 28: Migration run, live verification, SRS as-built

- [ ] **Step 1:** Full gates: backend `uv run pytest`, ruff, mypy; frontend `npm test -- --run`, typecheck, lint, build.
- [ ] **Step 2:** Workspace-copy check: copy `papers_cache` + `paperhub.db`, run `paperhub-migrate-idea-notes --dry-run`, then for real, then `paperhub-reanchor-marks`; diff `source.html` and `chunks` against the original — empty; every idea with a non-empty `note` has an idea note with equal text.
- [ ] **Step 3:** Run `backend/scripts/reingest_all_papers.ps1 -DryRun` on the live workspace, then the two new steps for real; the summary shows every idea migrated or `skipped_exists`, zero `error`.
- [ ] **Step 4:** Live gate on `:8000` + the dev server (I-8 #12, H5b.1 parts), on a paper ingested **before** this branch: highlight in HTML and PDF, a figure mark, an equation mark, a box on a PDF page, a highlighter stroke with a skipped word, a mark on a translation (then translation off → 文 marker → Turn on); close and reopen the app and a second session on the same paper shows them; quote → source chip → reader → back pill; delete → Undo → Delete again; "summarise what we concluded about §2 into my notes" → the card → Undo → Apply again; edit that summary → the next turn's trace shows it in `<notes_changes>`; on a note longer than one page the agent reaches the right block by outline or search; a search turn after reading a note sends nothing repeating it; export Markdown and Word. Read each run's `tool_calls`; ask the user to confirm in the browser.
- [ ] **Step 5:** Add an **As built (H5b.1)** paragraph to the SRS H5b section (what shipped, implementation calls, deviations) and update the design README; commit `docs(srs): record H5b.1 as built`. No version bump — H5b releases once, after H5b.3.
