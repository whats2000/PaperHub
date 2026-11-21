# H5b.1 — Tables, `[[` links and the table of contents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tables, `[[` links (to notes, papers and headings) and a table of contents (floating rail + `toc` block) to the H5b.1 notes editor, end to end: editor, store, search, find, export, agent read/write, change card.

**Architecture:** Tables are BlockNote's own `table` block, stored as its `tableContent` object in `note_blocks.payload_json`; the backend learns to validate, index, export and read it. `[[` links are a new inline node `noteLink` modelled on `mentionInline` / `markCite`, fed by two new read endpoints. The table of contents is one outline function used by a floating rail in the notes pane and by a `toc` block.

**Tech Stack:** BlockNote 0.57.0 (core / react / shadcn), React, zustand, Vitest + RTL; FastAPI, Pydantic, SQLite, pytest; pandoc for `.docx`.

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md`, §III-10.5 H5b, bullet **"Tables, `[[` links and the in-note table of contents"** (under "The note editor works like Notion"). That bullet is the authority; this plan argues from it.

## Global Constraints

- Worktree `D:\GitHub\PaperHub-h5b`, branch `feat/plan-h5b1-notes-core`. Never touch `D:\GitHub\PaperHub` files. Never write the live workspace DB (`D:\GitHub\PaperHub\backend\workspace\paperhub.db`); live checks use a copy.
- One-to-one with Markdown: no merged cells, no cell background / text colour. Cell props other than `textAlignment` stay default; `colspan` / `rowspan` stay 1.
- Yellow is reserved for citations: `noteLink` chips use the text colour with an underline; dead links grey + strikethrough.
- i18n: every user-facing string through `t("notes:<key>", "English")`, keys added to all four locales (`frontend/src/locales/{en,zh-TW,zh-CN,ja}/notes.json`); `parity.test.ts` must pass.
- Every appear / disappear animates in the direction the user expects; exits too; `prefers-reduced-motion` respected. Reuse `ui/` popover animation classes for overlays.
- Agent-flow record principle: new or changed agent tool behaviour records enough in `record_result` to reconstruct it.
- Any change to how the model is instructed (the `edit_note` tool description, any `llm/prompts/*.yaml`) requires invoking the `writing-agent-prompts` skill first, by the agent doing the edit.
- TDD: failing test first. Per task run only the relevant test files + targeted `ruff` / `mypy` / `tsc`; full suites only at the end.
- Commits: Conventional Commits, body wrapped at 72, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push, no merge.
- No new per-paper data, no schema change (`note_blocks.type` has no CHECK) → no migration step.
- Untracked `backend/src/paperhub/notes/markdown_inline.py` and its test are NOT part of this plan; do not stage them.

## Review Focus

1. **An old note with no tables / links** opens, edits and saves exactly as before (no op churn from the schema change; `diffDocuments` emits nothing on load).
2. **A table pasted from Excel / a web page / Markdown text** becomes a `table` block with a header row and no colours or merged cells left in it (colours / spans stripped on paste).
3. **A `[[` link to something deleted** (note soft-deleted, heading block deleted, paper removed) renders dead with no error toast and no console error; clicking it does nothing harmful.
4. **The agent edits a note that contains a table and links** (`update` of a neighbouring paragraph, `update` of the table with one cell changed): untouched cells keep rich runs, `noteLink` nodes survive, nothing else in the note changes.
5. **Ctrl+F matches text inside table cells and link labels**, and Enter moves to them (the current match scrolls into view and is selected).

---

## Phase 1 — Tables

### Task 1: Backend accepts, validates and indexes table blocks

**Files:**
- Modify: `backend/src/paperhub/notes/models.py` (`BlockType`, `Block.content`, new `check_table`)
- Modify: `backend/src/paperhub/notes/ops.py` (`BlockIn.content` type + validator, `_Row.content`)
- Modify: `backend/src/paperhub/notes/store.py` (`_runs_text` / `block_text` table branch)
- Modify: `frontend/src/types/notes.ts` (`NoteBlockType` += `"table"`, `NoteContent` += `TableContent`)
- Modify: `frontend/src/lib/notes/ops.ts` (`blockText` mirror)
- Test: `backend/tests/notes/test_table_blocks.py`, `frontend/src/lib/notes/ops.test.ts`

**Interfaces:**
- Produces: `check_table(content: dict[str, Any]) -> dict[str, Any]` (raises `ValueError` with a reason); `table_rows(content: dict) -> list[list[list[dict]]]` (rows → cells → runs, cells normalised from `str` / runs / `{type:"tableCell", props, content}`), in `models.py`, used by Tasks 4, 5. `block_text("table", props, content)` = rows joined by `"\n"`, cells by `" | "`, each cell's runs through `_runs_text`. Frontend `TableContent` type `{type:"tableContent"; columnWidths:(number|null)[]; headerRows?:number; headerCols?:number; rows:{cells:TableCell[]}[]}`.

- [ ] **Step 1: Write failing tests** in `test_table_blocks.py`: (a) `POST /notes/{id}/ops` insert of `{type:"table", content:{type:"tableContent", columnWidths:[null,null], headerRows:1, rows:[{cells:["指標","值"]},{cells:[[{"type":"text","text":"ORR","styles":{"bold":true}}], "62%"]}]}}` succeeds and `GET /notes/{id}` returns the same content; (b) the block's `note_blocks.text` == `"指標 | 值\nORR | 62%"` and FTS finds `ORR`; (c) rejects (422 with reason) ragged rows, `headerRows: 2`, a cell with `backgroundColor:"red"`, `colspan:2`, a non-`tableContent` dict, a `markCite` run with empty `markId` inside a cell; (d) an existing paragraph insert/update is unchanged (regression).
- [ ] **Step 2: Run** `uv run pytest tests/notes/test_table_blocks.py -v` → FAIL.
- [ ] **Step 3: Implement.** `BlockType` += `"table"`; `content: list[dict[str, Any]] | dict[str, Any] | str | None` in `Block`, `BlockIn`, `_Row`; `BlockIn`'s content validator calls `check_table` when the type is `table` (dict required) and `check_inline` for each cell's runs; `headerRows` / `headerCols` ∈ {absent, 0, 1}; `columnWidths` length == column count, items int or None.
- [ ] **Step 4: Frontend mirror**: types + `blockText` table branch; add a case to `ops.test.ts` asserting the same text as (b).
- [ ] **Step 5: Run** the two test files + `uv run ruff check src tests` + `uv run mypy src` + `npm run typecheck` → PASS.
- [ ] **Step 6: Commit** `feat(notes): store and index table blocks`.

### Task 2: Tables in the editor, the Notion way

**Files:**
- Modify: `frontend/src/components/notes/schema.ts` (add `table` from `defaultBlockSpecs`)
- Create: `frontend/src/components/notes/tableKeys.ts` (Tab in last cell adds a row; paste clean-up)
- Create: `frontend/src/components/notes/NoteTableHandles.tsx` (custom handle menus: insert / delete / align, header toggles in the table ⋯ menu; no colour / split)
- Modify: `frontend/src/components/notes/NoteEditor.tsx` (`tableHandles={false}` on `BlockNoteView` + render `NoteTableHandles`; `/table` slash item override: 3 × 3, `headerRows: 1`)
- Modify: `frontend/src/index.css` (scroll box + edge fades, header tint, grow / shrink animations, reduced motion)
- Modify: `frontend/src/components/notes/guideData.ts`, the four `notes.json`
- Test: `frontend/src/components/notes/tables.test.tsx`

**Interfaces:**
- Consumes: Task 1's `TableContent`.
- Produces: `addRowAfterLastCell(editor): boolean` and `cleanPastedTable(content: TableContent): TableContent` (strip colours, spans → 1, header row 1 when none) in `tableKeys.ts`.

- [ ] **Step 1: Write failing tests** (`tables.test.tsx`, using `testEditor.ts`): `/table` inserts a 3 × 3 table with `headerRows: 1`; Tab in the last cell adds a row and the caret is in its first cell; Shift+Tab moves back; pasting the HTML of a 2 × 2 table with a coloured, `colspan=2` cell yields a table whose cells have default colours and span 1; pasting GFM text `| a | b |\n|---|--:|\n| 1 | 2 |` yields a table with column 2 `textAlignment: "right"`; the column handle menu offers Insert left / right, Align left / centre / right, Delete and no colour item; the table ⋯ menu toggles header row / header column.
- [ ] **Step 2: Run** `npx vitest run src/components/notes/tables.test.tsx` → FAIL.
- [ ] **Step 3: Implement** schema + handles + keys + slash override. Build the handle menus on BlockNote's `TableHandlesController` with its `tableHandle` menu components replaced (check its props in `node_modules/@blocknote/react/src/components/TableHandles/`); alignment sets `textAlignment` on every cell of the column. Markdown paste: if BlockNote's paste handler does not turn GFM text into a table, parse it with BlockNote's `tryParseMarkdownToBlocks` in a `pasteHandler`.
- [ ] **Step 4: CSS**: table in an `overflow-x: auto` box; left / right fade shown only when clipped on that side; header cells tinted + bold; inserted row grows from 0 height, inserted column from 0 width, deleted ones shrink (~200 ms ease-out; none under reduced motion). Verify the drag-to-reorder and "+" extend buttons still work with the custom handles.
- [ ] **Step 5: i18n + guide**: slash title / aliases ("table", "表格", "表"), handle menu items, guide entries (Tab / Shift+Tab, Tab in the last cell, handles).
- [ ] **Step 6: Run** the test file + `parity.test.ts` + `NoteEditor.test.tsx` + `npm run typecheck` + `npm run lint` → PASS.
- [ ] **Step 7: Commit** `feat(notes): tables in the note editor`.

### Task 3: Find and citations inside table cells

**Files:**
- Modify: `frontend/src/components/notes/noteFind.ts` (`blockId` via walking up to `blockContainer`)
- Modify: `frontend/src/components/notes/citationDecorations.ts` only if cell text is not decorated today
- Test: `frontend/src/components/notes/NoteFind.test.tsx`, `citationDecorations.test.ts`

- [ ] **Step 1: Failing tests**: a match inside a table cell has the table block's id as `blockId` and becomes current with Enter; a `[chunk:12]` marker inside a cell renders as a citation chip.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (reuse `blockIdAt` from `blocks/citeForms.ts`). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `fix(notes): find and citation chips inside table cells`.

### Task 4: Export tables

**Files:**
- Modify: `backend/src/paperhub/notes/export.py` (`_block` table branch; `_cited` / `export_sources` look inside table cells)
- Modify: `backend/src/paperhub/notes/pandoc.py` (`_READER` += `+pipe_tables`)
- Test: `backend/tests/notes/test_export_tables.py`

**Interfaces:** Consumes `table_rows` (Task 1).

- [ ] **Step 1: Failing tests**: Markdown export of the Task 1 table is `| 指標 | 值 |\n| --- | --- |\n| **ORR** | 62% |`; alignment row from each column's header-cell `textAlignment` (`:--`, `:-:`, `--:`; default `---`); a `|` in a cell is written `\|`; a line break in a cell becomes `<br>`-free text (a space); a table with `headerRows: 0` gets an empty header row; a `markCite` in a cell gets its footnote; `.docx` export (pandoc present — skip otherwise, as the existing docx tests do) contains a `w:tbl` with 2 rows.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** test file + existing `tests/notes/test_export*.py` → PASS.
- [ ] **Step 5: Commit** `feat(notes): export tables to Markdown and Word`.

### Task 5: The agent reads and writes tables

**Files:**
- Modify: `backend/src/paperhub/agents/core/note_tools.py` (`_block_xml` table body; `edit_note` table content from a GFM string; per-cell merge; `_check_runs` into table cells; `_summary` → `tables_added: [{rows, cols}]`; `edit_note` tool schema description)
- Create: `backend/src/paperhub/notes/gfm_table.py` (`parse_gfm_table(text: str) -> dict` returning `tableContent`, `table_to_gfm(content: dict, quotes) -> str`)
- Modify: `backend/src/paperhub/notes/ops.py` (`_merged`: a table update keeps each unchanged cell's runs via `preserve_runs`)
- Modify: `frontend/src/components/chat/NotesChangeCard.tsx`, `frontend/src/types/domain.ts` (`NotesChangedData.summary.tables_added`), four `notes.json` (`card.addedTable`: "Added a table ({{rows}} rows × {{cols}} columns)")
- Test: `backend/tests/notes/test_gfm_table.py`, `backend/tests/agents/core/test_note_tools_tables.py` (or the existing note-tools test module), `frontend/src/components/chat/NotesChangeCard.test.tsx`

**Interfaces:**
- Consumes: `table_rows`, `check_table` (Task 1); `preserve_runs` (`notes/inline_merge.py`).
- Produces: `parse_gfm_table` / `table_to_gfm` (also used by H5c later).

- [ ] **Step 1: Invoke the `writing-agent-prompts` skill** before touching the `edit_note` description; the description gains one line + one example for a table block (content = GFM pipe table, first row header). Run its ≥2 variants × query set comparison on the deploy model for "make a table comparing X and Y in my note" style requests; record the result in the task report.
- [ ] **Step 2: Failing tests**: `parse_gfm_table` round-trips with `table_to_gfm` for alignment, escaped `\|`, empty cells, CJK; rejects ragged rows / missing delimiter row with a reason; `read_note` shows a table as `<note_block id=… type="table" author=…>` + GFM body; `edit_note` insert of `{type:"table", content:"| a | b |\n|---|---|\n| 1 | 2 [chunk:N] |"}` creates the block, and an unsourced `[chunk:999]` in a cell is rewritten `[unsourced:…]` as in paragraphs; an `update` changing one cell keeps a bold run and a `markCite` in another cell; the change-set summary has `tables_added: [{"rows": 2, "cols": 2}]` (header counted as a row) and the trace `record_result` includes it; the change card shows "Added a table (2 rows × 2 columns)".
- [ ] **Step 3: Run** → FAIL. **Step 4: Implement.** **Step 5: Run** the test files + ruff + mypy + the card test → PASS.
- [ ] **Step 6: Commit** `feat(notes): let the agent read and write tables`.

## Phase 2 — `[[` links

### Task 6: Backend: `noteLink` node and link-target endpoints

**Files:**
- Modify: `backend/src/paperhub/notes/models.py` (`check_inline`: `noteLink` props), `store.py` (`_runs_text`: label, else resolved name — see below), `inline_merge.py` (`_INLINE_NODES` += `noteLink`, `node_text`), `agents/core/note_xml.py` (`<note_link …>` tag; `_has_cite` → any of markCite / noteLink), `notes/export.py` (`inline`: label text), `markdown_inline.py` (`_default_node` label) — the last only if it is imported by tracked code; it is untracked, leave it alone otherwise.
- Modify: `backend/src/paperhub/api/notes.py` (three GET endpoints, registered before `/{note_id}`)
- Test: `backend/tests/notes/test_note_links.py`

**Interfaces:**
- `noteLink` props: `targetKind: "note" | "paper"`, `targetId: int` (> 0), `blockId: str` ("" = none), `sectionKey: str` ("" = none; paper sections only), `label: str` (display text, "" = follow the target). The node also stores `name: str`, the target's name when inserted, used as text when `label` is "" (search, export, agent read) so the backend never needs a lookup.
- `GET /notes/search?q=&limit=20` → `list[NoteHit{note_id, kind: "paper"|"session"|"idea", target_id, title}]` (display title via `display_title`, live notes only, case-insensitive substring, recent first).
- `GET /notes/headings/search?q=&limit=20` → `list[HeadingHit{note_id, note_title, block_id, text, level}]`.
- `GET /notes/{note_id}/outline` → `list[OutlineItem{block_id, type: "heading"|"section", text, level}]` (section level 1, heading `props.level`, document order incl. nested children; 404 for a deleted note).
- `GET /notes/blocks/{block_id}` (exists) answers whether a heading still exists.

- [ ] **Step 1: Failing tests**: ops validation accepts a valid `noteLink` and rejects bad `targetKind`, `targetId: 0`, non-str label; block text and FTS use `label or name`; `read_note` renders `<note_link kind="note" id="12" block="b_…">RoboTTT › 閘控</note_link>`; an agent `update` of the same text keeps the node (kept list names it); Markdown export writes the label text; the three endpoints return the shapes above, exclude deleted notes, and `outline` lists a nested heading under a toggle heading in order.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + ruff + mypy → PASS.
- [ ] **Step 5: Commit** `feat(notes): noteLink node and link-target endpoints`.

### Task 7: `noteLink` chip, preview and navigation

**Files:**
- Create: `frontend/src/components/notes/blocks/NoteLink.tsx` (inline spec), `NoteLinkView.tsx` (chip + hover preview with Open / Edit display text; dead state)
- Create: `frontend/src/lib/notes/reveal.ts` (`revealBlock(root: HTMLElement, blockId: string, opts?: {unfold?: (blockId: string) => void}): Promise<boolean>` — extracted from `NotesPane.tsx:130-165`, same scroll + `data-flash` + give-up timer)
- Create: `frontend/src/lib/notes/openNoteLink.ts` (`openNoteLink(props)`: paper → `openPaper` (+ section jump using the reader's existing section navigation if any); note → `getNote` → by `target_kind`: paper → `useNotesPaneStore.openAt({kind:"paper", …, blockId})`; session → navigate to that chat, then `openAt({kind:"session", …})`; idea → the Ideas page with that idea selected and the block revealed in its editor)
- Modify: `NotesPane.tsx` (use `revealBlock`; honour a `session` request only for the active chat — routing to another chat is done by `openNoteLink` first), `schema.ts`, `noteFind.ts` (`inlineText` `noteLink` → label or name), `frontend/src/lib/notes/ops.ts` (`runsText` label or name), four `notes.json`
- Test: `frontend/src/components/notes/noteLink.test.tsx`, `frontend/src/lib/notes/reveal.test.ts`

**Interfaces:** Consumes Task 6 endpoints (add `searchNotes`, `searchNoteHeadings`, `getNoteOutline` to `frontend/src/lib/api.ts`).

- [ ] **Step 1: Failing tests**: chip renders icon + name (+ " › heading") or the label; underline, text colour (class assertion, no yellow token); hover shows the preview with path and Open / Edit display text; editing the display text updates `label`; a 404 from the target lookup renders the dead state (grey + line-through, preview "This note was deleted") with no toast; clicking a paper link calls `openPaper`; clicking a note link of a paper note calls `openAt` with the block id; `revealBlock` scrolls + flashes and calls `unfold` when the block is in a folded part; Ctrl+F matches the label as one unit.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Chip resolves its current name lazily (outline / note lookup, cached per session in the notes store) when `label` is ""; preview open / close animate like `ui/popover`.
- [ ] **Step 4: Run** + typecheck + lint + parity → PASS.
- [ ] **Step 5: Commit** `feat(notes): [[ link chips that open notes, papers and headings`.

### Task 8: The `[[` picker

**Files:**
- Create: `frontend/src/components/notes/linkPicker.ts` (`parseLinkQuery(q: string): {mode: "targets"|"noteHeadings"|"thisNote"|"allHeadings"; name: string; heading: string; label: string}`; `linkItems(editor, noteId, q)`)
- Modify: `NoteEditor.tsx` (a `SuggestionMenuController` for `[[`; an input rule / key handler that opens it when a second `[` is typed and removes the two brackets), guide data, four `notes.json`
- Test: `frontend/src/components/notes/linkPicker.test.ts`, `noteLink.test.tsx` (picker flow)

- [ ] **Step 1: Failing tests**: `parseLinkQuery` — `"robo"` → targets; `"robo#閘"` → noteHeadings; `"#動"` → thisNote; `"##閘"` → allHeadings; `"robo|見此"` → label "見此"; typing `[[` in the editor opens the menu with groups Notes (icon per kind) and Papers; `[[RoboTTT#` lists that note's outline, and a paper's `#` lists its sections (`GET /papers/content/{id}/sections`); Enter inserts a `noteLink` with the right props and a trailing space, and `|text` sets `label`; Escape closes leaving the typed text; no "create" item (H5b.2).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + typecheck + lint + parity → PASS.
- [ ] **Step 5: Commit** `feat(notes): the [[ link picker`.

## Phase 3 — Table of contents

### Task 9: Outline and the `toc` block

**Files:**
- Create: `frontend/src/lib/notes/outline.ts` (`noteOutline(blocks: EditorBlockLike[]): OutlineEntry[]`, `OutlineEntry{blockId, text, level: 1|2|3}`: headings 1–3 and `section` blocks at level 1, document order incl. children)
- Create: `frontend/src/components/notes/blocks/TocBlock.tsx` (`createReactBlockSpec({type:"toc", propSchema:{}, content:"none"})`, live list from the editor document, click → `revealBlock`)
- Modify: `schema.ts`, `atomCaret.ts` (`ATOM_CARET_TYPES` += `toc`), `NoteEditor.tsx` (slash item "Table of contents" / "目錄"), guide data, four `notes.json`
- Modify: backend `models.py` (`BlockType` += `"toc"`), `export.py` (`toc` → a nested list of the headings, linked as `[text](#slug)` with pandoc-style slugs), `note_tools.py` (`toc` reads as `<note_block … type="toc"/>`), `frontend/src/types/notes.ts`
- Test: `frontend/src/lib/notes/outline.test.ts`, `frontend/src/components/notes/tocBlock.test.tsx`, `backend/tests/notes/test_toc_block.py`

- [ ] **Step 1: Failing tests**: `noteOutline` levels / order / nested heading / skips h4+ and empty headings; the block re-renders when a heading is added or renamed; click reveals the heading; the block's content cannot be typed into (atom caret); backend accepts and exports `toc`; agent reads it as an empty `toc` block.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + gates for touched files → PASS.
- [ ] **Step 5: Commit** `feat(notes): table of contents block`.

### Task 10: The floating table of contents

**Files:**
- Create: `frontend/src/components/notes/TocRail.tsx` (rail + slide-out list; scroll spy on the pane's scroll element; click → `revealBlock` with unfold)
- Modify: `NotesPane.tsx` (render `TocRail` inside the panel), `NotesToolbar.tsx` (⋯ → "Show table of contents" check item), `frontend/src/store/notes.ts` (`tocOn: boolean`, default true, persisted in `partialize`), guide data, four `notes.json`
- Test: `frontend/src/components/notes/TocRail.test.tsx`, `NotesToolbar.test.tsx`

- [ ] **Step 1: Failing tests**: hidden with < 2 entries; one line per entry with level-dependent width; hover opens the list (slides from the right, overlays, no layout shift), leaving closes it after a short delay with a reverse slide; click reveals + flashes and unfolds a folded part; the entry whose heading is at or above the pane's top + 90 px is marked current; the toolbar toggle hides the rail and is remembered across reloads; the rail does not take content width (absolutely positioned).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Make sure Follow reading's programmatic scroll only updates the spy, never fights it.
- [ ] **Step 4: Run** + typecheck + lint + parity → PASS.
- [ ] **Step 5: Commit** `feat(notes): floating table of contents`.

### Task 12: Table style setting (three-line / grid)

**Files:**
- Modify: `frontend/src/store/notes.ts` (`tableStyle: "threeLine" | "grid"`, default `"threeLine"`, persisted in `partialize` beside `focusOn` / `followOn`), `NotesToolbar.tsx` (⋯ menu: "Table style" with Three-line / Grid radio items), `NoteEditor.tsx` (editor root carries `data-table-style`), `frontend/src/index.css` (three-line and grid rules; faint editing lines on `:focus-within` / `:hover`; any transition off under reduced motion), `useNoteExport.ts` (pass the style to export), four `notes.json`, `guideData.ts`
- Modify: `backend/src/paperhub/api/notes.py` (export accepts `table_style=threeLine|grid`, default threeLine), `notes/export.py` / `notes/pandoc.py` (docx table borders by style; Markdown unchanged)
- Test: `frontend/src/components/notes/NotesToolbar.test.tsx`, `tables.test.tsx`, `useNoteExport.test.tsx`, `backend/tests/notes/test_export_tables.py`

- [ ] **Step 1: Failing tests**: default style is three-line; the ⋯ menu switches it, it persists across a store rehydrate, the editor root carries `data-table-style`; export requests carry `table_style`; backend rejects an unknown style (422); a three-line docx table has top, header-bottom and bottom borders and no vertical / inner horizontal borders; a grid docx table has all borders.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (docx borders: post-process the table XML pandoc writes, or a reference-docx table style — pick the smaller change, keep the two-run sandbox flow, document it). **Step 4: Run** → PASS + typecheck / lint / parity / ruff / mypy.
- [ ] **Step 5: Commit** `feat(notes): three-line and grid table styles`.

### Task 13: Table caption and notes

**Files:**
- Modify: `frontend/src/components/notes/schema.ts` (table spec propSchema `caption: ""`, `notes: ""` — no other props), `NoteTableHandles.tsx` (table ⋯ menu: Add / Remove caption, Add / Remove notes), a new `TableCaption.tsx` rendering "Table N." + editable caption above and editable notes below (inline `$…$` via KaTeX like `inlineMath`), `noteFind.ts` (caption / notes searchable), `lib/notes/ops.ts` (`blockText` includes them), four `notes.json`, `guideData.ts`
- Modify: backend `notes/models.py` / `ops.py` (table props `caption`, `notes`: strings, ≤ 2,000 chars; others still rejected), `notes/store.py` (block text includes caption first and notes last), `agents/core/note_tools.py` (`<note_block … type="table" caption="…">` + notes after the GFM body; agent `update` keeps them via the prop merge), `notes/export.py` + `pandoc.py` (`+table_captions`; Markdown `Table: <caption>` line + notes paragraph; docx caption + notes paragraph)
- Test: `tables.test.tsx`, `NoteFind.test.tsx`, `backend/tests/notes/test_table_blocks.py`, `test_export_tables.py`, `backend/tests/agents_core/test_note_tools_tables.py`

- [ ] **Step 1: Failing tests**: Add caption shows an editable line "Table 1." above the table with the caret in it; a second captioned table above it renumbers both; emptying the caption removes it and the menu says Add again; notes render below in muted text and take `$p<0.05$` as math; Ctrl+F finds caption and notes text; backend stores / validates the props and FTS finds caption words; `read_note` shows `caption="…"` and the notes; Markdown export writes `Table: …` and the notes paragraph; docx has a table caption and the notes paragraph.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS + gates for touched files.
- [ ] **Step 5: Commit** `feat(notes): table captions and notes`.

### Task 14: Nothing is created by looking (user-reported ghost chats)

**Files:**
- Modify: `frontend/src/components/notes/NotesPane.tsx` (drop the display-time `ensureBackendSession`; an unsaved chat / a target with no note renders an empty editor), `frontend/src/store/notes.ts` (`loadTarget` handles "no note yet"; the first save creates the chat via `ensureBackendSession` when needed, then the note, then posts the ops — one path also used by marks and the note card), `frontend/src/lib/api.ts`, `lib/notes/openNoteLink.ts` + `NoteLinkView.tsx` (gone vs unreachable wording + Retry), `frontend/src/index.css` / pane (`overflow-x: clip` on the notes scroll area), four `notes.json`
- Modify: `backend/src/paperhub/api/notes.py` (`GET /notes/target` no longer creates: returns `{note: null}` or 404 per existing client contract — pick one and update callers; new `POST /notes/target` creates idempotently), `backend/src/paperhub/notes/store.py` (`list_live_notes` excludes empty notes and notes of chats the sidebar hides, unless a `noteLink` points at them), `backend/src/paperhub/api/sessions.py` (list a chat whose note has content; title = note's first line while the chat title is still "New chat" and it has no messages), every backend caller of `get_or_create_note` reviewed (marks, agent `edit_note`, idea notes keep creating on write)
- Create: `backend/src/paperhub/cli/clean_empty_chats.py` (`paperhub-clean-empty-chats [--dry-run]`, entry point in `pyproject.toml`), step in `backend/scripts/reingest_all_papers.ps1`
- Test: `backend/tests/notes/test_note_create_on_write.py`, `backend/tests/test_clean_empty_chats.py`, `backend/tests/api/test_sessions_list.py` (or the existing sessions test module), `frontend/src/components/notes/NotesPane.test.tsx` (or ChatPage.notes.test.tsx), `noteLink.test.tsx`

- [ ] **Step 1: Failing tests**: opening the notes pane on a new chat makes no `POST /sessions` and no note; typing the first character creates the chat + note and saves it; `GET /notes/target` on a paper with no note creates nothing; marks and agent edits still create the note; `[[` search omits an empty note and the note of a hidden empty chat, but lists an empty note that a `noteLink` targets; `GET /sessions` lists a no-message chat whose note has text, titled by its first line; clicking a gone link says deleted, an unreachable one says can't open + Retry (no error toast); the notes scroll area has `overflow-x: clip`; the cleanup CLI dry-run lists exactly the qualifying chats (fixtures: one qualifying, one with a message, one with a paper, one with note text, one linked, one renamed) and a real run soft-deletes only that one and logs it; running twice changes nothing.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS + ruff / mypy / typecheck / lint / parity.
- [ ] **Step 5: Commit** in focused commits (backend create-on-write + search; sessions list; cleanup CLI + script; frontend).
- [ ] **Step 6: Live workspace (controller, with user approval):** back up `paperhub.db`, run `paperhub-clean-empty-chats --dry-run` on the live workspace and show the user the list; run for real only after the user confirms.

## Phase 4 — Verification

### Task 11: Full gates and live check on a workspace copy

- [ ] **Step 1:** Backend `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`; frontend `npm test -- --run`, `npm run typecheck`, `npm run lint`, `npm run build`. All green (the two pre-existing `test_section_rename.py` failures must be investigated and fixed or shown pre-existing on `cc0cec33` with a ruling).
- [ ] **Step 2:** Copy the live workspace (DB + `papers_cache`) to a scratch dir; run an own backend on `:8110` and Vite on `:4173` against it (the user's servers are not touched). Headless Chromium: on a paper ingested before this branch, insert a table via `/table`, Tab past the last cell, paste a GFM table, align a column; type `[[`, link a heading in another note and a paper, click both; hover a dead link; use the rail and the `/目錄` block; Ctrl+F inside a cell; export Markdown and Word and open both. Screenshot each, both themes.
- [ ] **Step 3:** One real agent turn on the copy (`POST /chat`): "在筆記裡做一個表格比較 X 和 Y" on a paper note; read the run's `tool_calls` (`notes:edit`): table inserted, citations checked per cell, change card "Added a table (r × c)".
- [ ] **Step 4:** Fix everything found (each fix TDD + commit), then write `.superpowers/tables-links-toc-report.md` listing checks, screenshots and fixes.
