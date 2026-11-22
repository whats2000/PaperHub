# H5b.1 — Table presets, per-line borders, merged cells Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the three approved demo features `table-presets`, `table-borders` and `table-merge` into the notes editor, end to end: editor, store, agent read/write, export, and a migration for existing tables.

**Architecture:** Every line between cells is a border of its own (0 none, 1 thin, 2 thick), stored as one JSON string prop `borders` on the table block. It holds two positional line grids that mirror the demo's `TableW.Hl` / `Vl`. A ProseMirror plugin draws the lines as cell decorations and remaps them when rows or columns are inserted, deleted or moved. Merges are BlockNote's own `colspan` / `rowspan`. In text (agent read/write, the `<`/`^` markers) they are Obsidian Sheets Extended markers, produced over a shared *grid* view of `tableContent` (backend `table_grid`, frontend `tableGrid.ts`). Export writes merged tables as HTML in Markdown. Word gets real merged cells and each table's own per-edge borders, applied by a post-pass over pandoc's `.docx`. That replaces the global `table_style`.

**Tech Stack:** BlockNote 0.57 (`@blocknote/core` / `react`, `prosemirror-tables` via `TableHandlesExtension.mergeCells` / `splitCell`), React, zustand, Vitest + RTL; FastAPI, Pydantic, SQLite, pytest; pandoc (`.docx`).

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10.5 H5b, the **Tables** bullet, sentences **"Merge and split cells [user, 2026-11-22 …]"** and **"Table borders: two presets, every line settable [user, 2026-11-22 …]"**. They are binding. Design source (read only): the approved review demo `notes-demo.html` (scratchpad copy of https://claude.ai/artifact/2A5BNdWaZEK4LtAJXDviXP), class `TableW` and features `table-presets`, `table-borders`, `table-merge`. Port rules: `D:/GitHub/PaperHub/.claude/skills/demo-review-port/SKILL.md` step 7.

## Global Constraints

- Worktree `D:\GitHub\PaperHub-h5b`, branch `feat/plan-h5b1-notes-core`. No push, no merge. Never write the live workspace DB except Task 11 Step 6, with the user's approval and a backup.
- Do NOT stage the unrelated working-tree changes present when this plan was written: `frontend/src/components/notes/NotesPane.tsx`, `frontend/src/store/notesPane.ts`, `frontend/src/components/notes/NotesPane.test.tsx`, `backend/src/paperhub/notes/markdown_inline.py`, `backend/tests/notes/test_markdown_inline.py`.
- Border values: `0` none, `1` thin (1 px editor / 6 eighth-points Word), `2` thick (2 px editor / 12 eighth-points Word). Presets: **Paper** = thick top rule, thin rule under the header, thick end rule, nothing else, header bold with no tint. **Notion** = every line thin, header tinted. The first default is Paper.
- Missing or empty `borders` = Paper lines for the table's current shape, everywhere: editor, export, history, suggestions, old documents.
- Borders are kept out of the table's text, its FTS text, `read_note` and every agent-visible string.
- Merge markers in text: a cell holding only `<` joins the cell to its left. A cell holding only `^` joins the cell above. A literal whole-cell `<` / `^` is written `\<` / `\^`.
- No change to any agent prompt or tool-description text. If a task finds one unavoidable, the agent making the edit must invoke the `writing-agent-prompts` skill first and run its ≥2 variants × query set comparison.
- Cell colours stay unsupported (BlockNote `cellBackgroundColor` / `cellTextColor` off, colours still stripped).
- Yellow is reserved for citations. Border and guide colours come from `--note-table-rule` / `--note-table-guide`, which are mixed with the table's surface and are opaque.
- i18n: every user-facing string goes through `t("notes:<key>", "English")`, with the key in all four `frontend/src/locales/{en,zh-TW,zh-CN,ja}/notes.json`. `parity.test.ts` passes.
- Every appear / disappear animates the way the gesture implies, exits included. Overlays use the `ui/` popover classes (`data-open:animate-in fade-in-0 zoom-in-95` / `data-closed:animate-out fade-out-0 zoom-out-95`). Respect `prefers-reduced-motion`.
- Keep every shipped table behaviour the demo lacks: row/column ⠿ handles (insert, delete, align, drag), the "+" extend buttons, Tab / Shift+Tab, Tab in the last cell adds a row, caption + notes, wide-table scroll fades, the grow/shrink motion, paste clean-up (colours), the change card's "Added a table (r × c)".
- TDD: write the failing test first. Per task, run only the touched test files plus targeted `uv run ruff check` / `uv run mypy src` / `npm run typecheck` / `npm run lint`. Run the full suites only in Task 11. Python only via `uv`.
- Commits: Conventional Commits, body wrapped at 72, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, one concern per commit.

## Decisions and rulings (binding for every task)

1. **Shape of `borders`.** The prop is a JSON string (BlockNote props are primitives) holding `{"preset":"paper"|"notion","h":[…],"v":[…]}`.
   - `h` has R+1 strings of C digits: `h[r][c]` is the line above grid row `r` in grid column `c`, and `h[R]` is the bottom rule.
   - `v` has R strings of C+1 digits: `v[r][c]` is the line left of grid column `c` in grid row `r`.
   - R × C is the *grid* shape: merged cells counted by what they cover.
   - Why: it is positional and one-to-one with the demo's `Hl`/`Vl`, so shape-checking against the grid is trivial. It is small, diffable and carries no cell identity, so merge and split never touch it (lines inside a span are kept and come back on split). H5c's codec can move the same object into the per-block attribute map unchanged.
2. **Structure changes** (frontend plugin, Task 7). Rows and columns are aligned by LCS over row nodes / column signatures (`Node.eq`). Ties put inserted rows as late as possible, so Tab-append reads as "after the last".
   - If the old lines equal the preset's lines for the old shape, the new lines are the preset's lines for the new shape.
   - Otherwise, **insert at boundary b** (0 ≤ b ≤ R): the line at b stays above the new row. The exception is b = R: the bottom rule stays at the bottom. The new row's other line copies a body-interior neighbour:
     - `h[b]` when hr < b < R;
     - `h[b+1]` when b = hr and b+1 < R;
     - `h[R-1]` when b = R and R-1 > hr;
     - else the preset's body line (Paper 0, Notion 1).
     The new row's vertical lines copy the row above it (the row below when b = 0).
   - **Delete row r** removes the line below it, except for the last row, which removes the line above it (the bottom rule stays).
   - **Move row**: its vertical lines travel with it. Horizontal lines stay where they are, because rules mark positions.
   - Columns follow the same rules without the header terms.
   - If the rows cannot be aligned, the result is the preset's lines.
3. **Merged cell edges.** A merged cell's side is drawn at the max weight of the segments it covers. Borders… always sets every segment of a span, so they agree. Segments inside a span are never drawn or exported.
4. **Header depth.** With the header row on, the header covers row 0 plus every row a row-0 cell spans into. `headerRows` is stored as that depth (0…R), and merge/split recompute it. Paper's thin rule sits under the depth. `headerCols` stays 0/1.
5. **Escapes.** Only a cell whose whole text is `<` or `^` is escaped. `a<b`, `x^2` and `$p<0.05$` are written as is.
6. **Who sets borders.**
   - `/table` and a pasted table take the viewer's default preset.
   - An agent-inserted table has no `borders` (it renders as Paper; the viewer default lives only in the browser).
   - Agent ops can never set `borders`: the prop is stripped from agent props.
   - The backend fits `borders` to the content on every insert/update (`fit_borders`). The same grid shape keeps the lines. A different shape gets the stored preset's lines. Malformed JSON or values are rejected (422). A shape mismatch is repaired, not rejected, because history restores and agent reshapes legitimately produce it.
7. **Edit guides.** Every edge without a border is drawn `1px dashed var(--note-table-guide)` while the editor is editable. Read-only renders and exports never show them. Per the approved demo, the table ⋯ menu also has **Hide edit guides / Show edit guides**, a per-viewer pref persisted with the other notes prefs (default shown).
8. **Word.**
   - `tblBorders` are all `nil`, and each `w:tc` gets `w:tcBorders` from the lines.
   - Merges: `w:gridSpan` on the anchor, `w:vMerge w:val="restart"` on it and `w:vMerge` on covered cells below; covered cells in the anchor's row are removed.
   - Rows < header depth are bold and marked `w:tblHeader`. Notion tints them `F2F2F2`.
   - A header-less table's empty pandoc header row is removed, since an empty row would sit outside the top rule.
9. **Markdown HTML table.**
   - Structure: `<table>`, `<caption>` (when it has a caption), `<thead>` with the header-depth rows as `<th>`, then `<tbody>`. `colspan` / `rowspan` only when > 1. `align="center|right"` from the column alignment.
   - Cell inline content: bold → `<strong>`, italic → `<em>`, underline → `<u>`, strike → `<s>`, code → `<code>`, link → `<a href>`. Everything else (math `$…$`, citation footnote markers, mentions) is written as the GFM cell writes it, HTML-escaped.
   - The notes paragraph follows as today.
10. **Column alignment with merges** is the alignment of the first cell that starts in that column and spans only it (default otherwise). `table_aligns` is replaced by a grid-based `column_aligns`.
11. **No toast on merge.** Merge is easy to undo (Ctrl+Z / Split), and Undo toasts are only for hard-to-restore actions.
12. **Migration scope.** The migration covers live table blocks of live notes, as one `system` change set per note (the `paperhub-reconcile-mark-blocks` precedent). Tables in trashed notes, history and suggestions rely on the missing = Paper rule.

## Review Focus

1. **Deleting a row that holds or crosses a merged cell.** prosemirror-tables shrinks or moves the span. The `borders` shape must still match the grid, the bottom rule must stay at the bottom, and nothing may throw. (Test: Task 7 Step 1 (e); Task 9 Step 1 (d).)
2. **Tab in the last cell of a Paper table**, including one whose last row is a merged `^` row. The new row appears and the thick end rule is under it, not above it. (Test: Task 7 Step 1 (b).)
3. **An agent `update` of a merged table that changes one cell's text.** The merges (`<`/`^`), the other cells' rich runs and the borders all survive. A literal `<` cell from the agent written as `\<` stays text. A stray `^` in row 0 is refused with a reason. (Test: Task 3 Step 1.)
4. **Export round-trip of a merged, custom-bordered table.** Markdown gives an HTML table with the right spans that pandoc reads back with the same spans. Word gives merged cells and exactly the drawn lines. (Test: Task 4 Step 1 (c), (e).)
5. **A table saved before this branch (no `borders`)** opens with no op churn, looks like Paper, exports like Paper, and only gets explicit lines when the user changes it or the migration runs. (Test: Task 7 Step 1 (a); Task 2 Step 1 (a); Task 5 Step 1.)

---

## Phase 1 — Backend

### Task 1: Grid view of a table; merged cells accepted

**Files:**
- Modify: `backend/src/paperhub/notes/models.py` (`_CELL_DEFAULTS` loses `colspan`/`rowspan`; new `table_grid`, `header_depth`, `column_aligns`; `check_table` validates the grid; `headerRows` 0…R)
- Modify: `backend/src/paperhub/agents/core/note_tools.py` (`_table_sizes`: `cols` = grid width)
- Modify: `backend/src/paperhub/notes/export.py` / `gfm_table.py` (callers of `table_aligns` → `column_aligns`; delete `table_aligns`)
- Test: `backend/tests/notes/test_table_blocks.py`

**Interfaces:**
- Produces (in `models.py`):
  - `@dataclass(frozen=True) class GridCell: row: int; col: int; rowspan: int; colspan: int; index: int` (`index` = position in `rows[row].cells`).
  - `table_grid(content: dict) -> tuple[list[list[GridCell | tuple[int, int]]], int, int]`: returns `(grid, R, C)`, where `grid[r][c]` is the anchor `GridCell` or `(ar, ac)`, the anchor that covers it. Raises `ValueError` with a reason on overlap, a span past the edge or a ragged grid.
  - `header_depth(content: dict) -> int`.
  - `column_aligns(content: dict) -> list[str]`.

- [ ] **Step 1: Write failing tests** in `test_table_blocks.py`:
  - (a) Insert the demo's two-level header (`rows[0].cells=[Method{rowspan:2}, 成功恢復{colspan:2}]`, `rows[1].cells=[Roof, Tire]`, then 3 body rows of 3, `headerRows: 2`). It is accepted and round-trips.
  - (b) `table_grid` of it has R=5, C=3. `grid[1][0] == (0, 0)` and `grid[0][2] == (0, 1)`.
  - (c) `header_depth` is 2 with `headerRows: 1`, and 0 with `headerRows: 0`.
  - (d) 422 with a reason for: a colspan running past the width, two spans overlapping, `rowspan: 0`, `colspan: "2"`, `headerRows: 6` on a 5-row table.
  - (e) `column_aligns` of a table whose row-0 cell spans columns 1–2 reads the alignment of column 2 from row 1.
  - (f) The block text of (a) is `"Method | 成功恢復\nRoof 擾動 | Tire 擾動\n…"` (real cells only).
  - (g) Every existing test in the file passes, with the old "colspan rejected" case flipped to accepted.
- [ ] **Step 2: Run** `uv run pytest tests/notes/test_table_blocks.py -v` → FAIL.
- [ ] **Step 3: Implement** `table_grid` (walk rows, place each cell at the first free column, mark its span), `header_depth`, `column_aligns`, and the `check_table` grid check. Point `_table_sizes`, `export._table`, `gfm_table.table_to_gfm` and `export._written_rows` at the grid width / `column_aligns`.
- [ ] **Step 4: Run** the test file + `tests/notes/test_export_tables.py tests/notes/test_gfm_table.py tests/agents_core/test_note_tools_tables.py` + `uv run ruff check src tests` + `uv run mypy src` → PASS.
- [ ] **Step 5: Commit** `feat(notes): accept merged table cells`.

### Task 2: Borders model, validation and fitting

**Files:**
- Create: `backend/src/paperhub/notes/table_borders.py`
- Modify: `backend/src/paperhub/notes/models.py` (`TABLE_TEXT_PROPS` stays caption/notes; `check_table_props` also accepts `borders` via `parse_borders`)
- Modify: `backend/src/paperhub/notes/ops.py` (`BlockIn._table_shape` and `_merged`: `fit_borders` on every table insert/update)
- Modify: `backend/src/paperhub/agents/core/note_tools.py` (agent table ops: `props.pop("borders", None)` before validation)
- Test: `backend/tests/notes/test_table_borders.py`, `backend/tests/agents_core/test_note_tools_tables.py`

**Interfaces:**
- Produces (in `table_borders.py`):
  - `Preset = Literal["paper", "notion"]`
  - `@dataclass(frozen=True) class Borders: preset: Preset; h: tuple[str, ...]; v: tuple[str, ...]`
  - `parse_borders(raw: str | None) -> Borders | None` (None for missing/empty; `ValueError` on bad JSON, preset, digits or ragged strings; at most 20 000 chars)
  - `dump_borders(b: Borders) -> str` (compact JSON, keys in the order preset, h, v)
  - `preset_lines(rows: int, cols: int, header_rows: int, preset: Preset) -> Borders`
  - `borders_of(props: dict, content: dict) -> Borders` (parsed, or Paper lines when missing)
  - `fit_borders(props: dict, content: dict) -> dict` (props with `borders` kept when its shape is the grid's, else the stored preset's lines; missing stays missing)
  - `edge(b: Borders, grid, side: Literal["top","bottom","left","right"], anchor: GridCell) -> int` (max over the span's segments)

- [ ] **Step 1: Write failing tests** in `test_table_borders.py`:
  - (a) `preset_lines(5, 3, 1, "paper")` → `h == ("222","111","000","000","000","222")`, every `v` row `"0000"`. `preset_lines(5, 3, 2, "paper").h[2] == "111"`. Notion is all `1`. With `headerRows` 0, Paper has only the top and bottom rules.
  - (b) `dump`/`parse` round-trip, and `ValueError` for `"x"`, `{"preset":"grid",…}`, digit `3`, wrong string count.
  - (c) The API accepts a table insert with valid `borders` and returns it unchanged. A bad `borders` → 422 with a reason.
  - (d) An update that adds a row while sending the old 3-row `borders` is stored with Paper lines for 4 rows (shape repaired, preset kept). A `notion` table reshaped gets Notion lines.
  - (e) An update with the same shape keeps a custom `h[2] == "202"`.
  - (f) A block's `text` and the FTS index never contain `preset`, `"h"` or digit runs from `borders`.
  - (g) In `test_note_tools_tables.py`: an agent `edit_note` insert of a table with `props: {"borders": …}` is stored with no `borders`. An agent `update` of a same-shape table keeps the stored custom borders. An agent `update` adding a row gets preset lines. `read_note` output contains no `borders`.
- [ ] **Step 2: Run** `uv run pytest tests/notes/test_table_borders.py tests/agents_core/test_note_tools_tables.py -v` → FAIL.
- [ ] **Step 3: Implement** `table_borders.py`. Wire `fit_borders` into `BlockIn` validation (inserts) and into `_merged` after `merge_props` / `merge_table` (updates, every actor). Strip agent `borders` in the op conversion next to `_gfm_block`.
- [ ] **Step 4: Run** the two files + `tests/notes/test_table_blocks.py` + ruff + mypy → PASS.
- [ ] **Step 5: Commit** `feat(notes): per-line table borders in the table's props`.

### Task 3: Merge markers in the agent's read and write

**Files:**
- Modify: `backend/src/paperhub/notes/gfm_table.py` (`table_to_gfm` writes the grid with markers + escapes; new `apply_merge_markers`; `parse_gfm_table` calls it)
- Modify: `backend/src/paperhub/notes/inline_merge.py` (`merge_table` keys cells by grid anchor `(row, col)`, keeps `new`'s spans, `headerRows` = `header_depth` when the old table had a header)
- Modify: `backend/src/paperhub/agents/core/note_tools.py` (`_TableError` reasons from marker parsing reach the tool result)
- Test: `backend/tests/notes/test_gfm_table.py`, `backend/tests/agents_core/test_note_tools_tables.py`

**Interfaces:**
- Consumes: `table_grid`, `header_depth` (Task 1); `fit_borders` (Task 2).
- Produces:
  - `apply_merge_markers(content: dict) -> dict` drops marker cells and sets `colspan`/`rowspan`, following the demo `TableW` constructor: `<` run → colspan, `^` run below the anchor → rowspan. A covered row's `<` cells join the `^` cell to their left. It raises `ValueError` for `<` in column 0, `^` in row 0, or a non-rectangular region, naming the row and column. It unescapes whole-cell `\<` / `\^`.
  - `table_to_gfm` writes covered cells as `<` (same row as the anchor, or right of the anchor's column in rows below) and `^` (the anchor's column in rows below). Text cells equal to `<`/`^` are written `\<` / `\^`.

- [ ] **Step 1: Write failing tests:**
  - `table_to_gfm` of Task 1's table is exactly:
    ```
    | Method | 成功恢復（每格 n = 20） | < |
    | --- | :-: | :-: |
    | ^ | Roof 擾動 | Tire 擾動 |
    ```
    followed by the body rows (alignment as stored).
  - `parse_gfm_table` of that text equals the stored spans.
  - A 2×2 block merge anchored at A writes row 1 as `| A | < |` and row 2 as `| ^ | < |`, exactly as the demo's `merge()` does, and parses back to `rowspan 2, colspan 2`.
  - A cell `\<` parses to the text `<`.
  - `a<b` and `$x^2$` cells are unchanged both ways.
  - Errors: `^` in row 0, `<` in column 0, and an L-shaped region each give a reason naming the cell.
  - In `test_note_tools_tables.py`:
    - `read_note` of a merged table shows the markers.
    - An agent `update` that rewrites one body cell keeps both merges, the other cells' bold runs and `markCite`, and the stored custom borders.
    - An agent insert of a table whose header row 2 sits under a `^` gets `headerRows: 2`.
    - An agent update with `| ^ | a |` in row 0 returns the error reason and writes nothing.
- [ ] **Step 2: Run** `uv run pytest tests/notes/test_gfm_table.py tests/agents_core/test_note_tools_tables.py -v` → FAIL.
- [ ] **Step 3: Implement.** `merge_table` matches old to new cells by anchor grid position, then by equal text (as today), and uses `table_grid` on both sides.
- [ ] **Step 4: Run** the two files + `tests/notes/test_table_blocks.py tests/notes/test_table_borders.py` + ruff + mypy → PASS.
- [ ] **Step 5: Commit** `feat(notes): merged cells as < and ^ in the agent's table text`.

### Task 4: Export — HTML for merged Markdown tables; Word per-edge borders and merged cells

**Files:**
- Modify: `backend/src/paperhub/notes/export.py`:
  - `_table`: merged → `_html_table`; else GFM as today.
  - New `_html_table(w, b) -> str` and `Writer.html_inline(runs) -> str`.
  - `note_to_markdown(..., merged_as: Literal["html", "grid"] = "html", table_layouts: list[TableLayout] | None = None)`. `"grid"` writes merged tables as GFM with empty covered cells.
- Modify: `backend/src/paperhub/notes/table_borders.py`: `@dataclass(frozen=True) class TableLayout: borders: Borders; spans: tuple[tuple[int, int, int, int], ...]; header_rows: int; preset: Preset; has_header: bool`, and `table_layout(props, content) -> TableLayout`.
- Modify: `backend/src/paperhub/notes/pandoc.py`:
  - Delete `TableStyle` / `TABLE_STYLES` / `_table_borders` / `_header_cell`.
  - `style_docx_tables(docx: bytes, layouts: Sequence[TableLayout]) -> bytes` styles the i-th `w:tbl` from `layouts[i]`.
  - `markdown_to_docx(md, resources, *, table_label, table_notes, table_layouts)`.
- Modify: `backend/src/paperhub/api/notes.py` (drop the `table_style` query param; docx path: `merged_as="grid"` + `table_layouts`)
- Test: `backend/tests/notes/test_export_tables.py`, `backend/tests/api/test_notes_export.py`

**Interfaces:**
- Consumes: `table_grid`, `header_depth`, `column_aligns` (Task 1); `borders_of`, `edge` (Task 2).

- [ ] **Step 1: Write failing tests:**
  - (a) Markdown of an unmerged table is byte-identical to today's (regression), whatever its `borders`.
  - (b) Markdown of Task 1's merged table is an HTML table: `<th rowspan="2">Method</th><th colspan="2" align="center">…</th>`, a 2-row `<thead>`, a `<caption>` when a caption is set, `<strong>ORR</strong>` for a bold run, `&lt;` for a literal `<`, and the notes paragraph after it.
  - (c) pandoc (skip when absent, as the other docx tests do) reads (b)'s HTML back as a table with the same spans.
  - (d) docx of a default (no `borders`) table: top and bottom `tcBorders` are `single` with `w:sz="12"`, the header-bottom is `w:sz="6"`, no left/right/insideV lines, header runs bold, no `w:shd` — the same look the old threeLine export gave.
  - (e) docx of a merged Notion table with one custom thick inner line:
    - one `w:gridSpan w:val="2"` and one `vMerge restart` + one `vMerge` continuation;
    - every remaining edge thin (`sz="6"`) except the custom one (`sz="12"`);
    - header rows tinted `F2F2F2` and marked `w:tblHeader`;
    - no edge drawn inside a span.
  - (f) docx of a header-less table has exactly R rows (pandoc's empty header removed).
  - (g) `GET /notes/{id}/export?format=docx&table_style=grid` succeeds and ignores the parameter. The old 422 test is replaced by this one.
  - (h) Captions are numbered and notes are small as before (existing tests pass).
- [ ] **Step 2: Run** `uv run pytest tests/notes/test_export_tables.py tests/api/test_notes_export.py -v` → FAIL.
- [ ] **Step 3: Implement.** The docx post-pass keeps the existing `_props` / `_with_child` schema-order helpers. Its row index offset is 0, because a header-less table's empty pandoc header row is removed first. `tcBorders` children go in CT_TcBorders order (`top, start/left, bottom, end/right`).
- [ ] **Step 4: Run** the two files + every `tests/notes/test_export*.py` + ruff + mypy → PASS.
- [ ] **Step 5: Commit** in two commits: `feat(notes): export merged tables as HTML in Markdown`, then `feat(notes): Word export draws each table's own borders and merges`.

### Task 5: Migration — Paper borders on every existing table

**Files:**
- Create: `backend/src/paperhub/cli/backfill_table_borders.py` (`paperhub-backfill-table-borders [--dry-run]`). Pattern: `cli/reconcile_mark_blocks.py` — `LIVE_NOTE_SQL`, one `system` change set per note via `apply_ops`, idempotent.
- Modify: `backend/pyproject.toml` (entry point)
- Modify: `backend/scripts/reingest_all_papers.ps1`: header comment `# 22. paperhub-backfill-table-borders   Plan H5b.1: every table without borders gets the Paper preset's lines (what it showed by default)`; every `N/21` label → `N/22`; `Invoke-Step "22/22 table borders" (@("paperhub-backfill-table-borders") + $dry)`.
- Test: `backend/tests/cli/test_backfill_table_borders.py`

**Interfaces:**
- Consumes: `preset_lines`, `dump_borders`, `parse_borders`, `table_grid`, `header_depth`.
- Produces: `async def backfill(db_path: Path, *, dry_run: bool) -> dict[str, Any]` with counts `checked`, `set`, `already`, `rejected` (list of `{note_id, block_id, reason}`).

- [ ] **Step 1: Write failing tests.** Fixtures:
  - a note with two tables without `borders` (one merged, `headerRows` 2);
  - a table with valid Notion borders;
  - a table in a trashed note;
  - a paragraph.

  Expected:
  - `--dry-run` → `checked 3, set 2, already 1`, and the DB is unchanged.
  - A real run sets Paper lines for the grid shape: the merged one has `h[2] == "111"`. It leaves the Notion table alone, writes one `system` revision for the note, and does not touch the trashed note.
  - A second run → `set 0`.
  - A block whose stored content is invalid lands in `rejected` with a reason, and the run continues.
- [ ] **Step 2: Run** `uv run pytest tests/cli/test_backfill_table_borders.py -v` → FAIL.
- [ ] **Step 3: Implement** + entry point + script step.
- [ ] **Step 4: Run** the test + ruff + mypy → PASS. Run `uv sync` so the entry point exists.
- [ ] **Step 5: Dry-run on a copy.** Run `uv run paperhub-copy-workspace` into a scratch dir (or copy `paperhub.db` and rewrite paths as the CLI does). Run the dry-run against the copy with `PAPERHUB_DB_PATH` pointed at it. Record the counts in the task report: every live table is either `set` or `already`, with zero `rejected`. Run it for real on the copy and run it again (`set 0`).
- [ ] **Step 6: Commit** `feat(notes): migrate existing tables to the Paper borders`.

## Phase 2 — Frontend

### Task 6: Grid and borders model (pure, mirrors the backend)

**Files:**
- Create: `frontend/src/components/notes/tableGrid.ts`, `frontend/src/components/notes/tableBorders.ts`
- Test: `frontend/src/components/notes/tableBorders.test.ts`

**Interfaces:**
- Produces (`tableGrid.ts`):
  - `type GridCell = { row: number; col: number; rowspan: number; colspan: number; index: number }`
  - `tableGrid(c: TableContent): { grid: (GridCell | [number, number])[][]; rows: number; cols: number }`
  - `headerDepth(c: TableContent): number`
- Produces (`tableBorders.ts`):
  - `type Weight = 0 | 1 | 2`; `type Preset = "paper" | "notion"`
  - `interface Borders { preset: Preset; h: string[]; v: string[] }`
  - `parseBorders(raw: unknown): Borders | null`; `dumpBorders(b: Borders): string`
  - `presetLines(rows: number, cols: number, headerRows: number, preset: Preset): Borders`
  - `bordersOf(props: Record<string, unknown>, c: TableContent): Borders` (missing → Paper)
  - `type Selection = { r0: number; r1: number; c0: number; c1: number }`
  - `normaliseSelection(c: TableContent, a: [number, number], b: [number, number]): Selection` (grows to whole spans, like the demo's `norm`)
  - `type BorderSet = "all" | "inner" | "innerH" | "innerV" | "outer" | "left" | "top" | "right" | "bottom" | "clear"`
  - `segments(b: Borders, c: TableContent, s: Selection, set: BorderSet): { axis: "h" | "v"; r: number; c: number }[]` (inner segments inside a span skipped, as the demo's `coveredH/V`)
  - `applyBorderSet(b: Borders, c: TableContent, s: Selection, set: BorderSet, weight: 1 | 2): Borders` (toggle: if every segment already has `weight`, they become 0; `clear` → 0)
  - `isPressed(...)` and `isUsable(...)` for the picker
  - `insertRow(b: Borders, at: number, headerRows: number): Borders`, `deleteRow(b, r)`, `moveRow(b, from, to)`, and the column equivalents, per ruling 2
  - `refit(old: Borders, oldShape: Shape, newShape: Shape, ops: StructureOp[]): Borders`
  - `cellEdges(b: Borders, grid, anchor: GridCell): { top: Weight; bottom: Weight; left: Weight; right: Weight }` (max over segments)

- [ ] **Step 1: Write failing tests** (values copied from the backend tests so the two sides agree):
  - `presetLines` cases from Task 2 (a).
  - `parseBorders` / `dumpBorders` round-trip (the string equals the backend's `dump_borders` for the same lines — pin the literal `{"preset":"paper","h":["222","111","000","222"],"v":["0000","0000","0000"]}`).
  - `insertRow` on Paper `[2,1,0,0,0,2]` (hr 1): at 5 → `[2,1,0,0,0,0,2]`; at 1 → `[2,1,0,0,0,0,2]`. On Notion it is all 1. Custom `h[3]=2` at 3 → both lines 2.
  - `deleteRow` of the last row keeps the bottom 2. `deleteRow(0)` keeps the top 2.
  - `moveRow` keeps `h` and moves that row's `v`.
  - `applyBorderSet` `inner` on a 2×2 selection sets 2 innerH + 2 innerV segments, and a second call clears them. With weight 2, `outer` makes the 8 outer segments 2.
  - `segments` on a selection containing a 2-row merge has no innerH segment inside the merge. `isUsable("innerH")` is false for a one-row selection.
  - `normaliseSelection` from a cell inside a merged block grows to its span.
  - `cellEdges` of a 2-column anchor with segment weights 0 and 2 → 2.
  - `headerDepth` cases from Task 1.
- [ ] **Step 2: Run** `npx vitest run src/components/notes/tableBorders.test.ts` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS + `npm run typecheck` + `npm run lint`.
- [ ] **Step 5: Commit** `feat(notes): table grid and per-line borders model`.

### Task 7: Draw the borders; keep them through structure changes

**Files:**
- Modify: `frontend/src/components/notes/schema.ts`:
  - `TABLE_PROP_SCHEMA` += `borders: { default: "" }`.
  - The `tableNode` attrs: `borders` parsed from / rendered to `data-borders`.
  - A derived `data-table-preset` (`paper` when empty) for CSS.
- Create: `frontend/src/components/notes/tableBorderView.ts`. The `tableBorderView` extension has two parts:
  - decorations: `Decoration.node` per cell with inline `border-*` styles from `cellEdges` (0 → guide when `view.editable`, else none);
  - an `appendTransaction` that, for each table whose grid shape no longer matches its `borders` (empty = Paper of the *old* shape), aligns old/new rows and columns (LCS over `Node.eq`, ties late) and writes `refit(...)` with `setNodeAttribute` in the same transaction. Undo therefore reverts both together.
- Modify: `frontend/src/components/notes/NoteEditor.tsx` (register `tableBorderView`; remove `data-table-style`)
- Modify: `frontend/src/index.css`:
  - Delete the `[data-table-style=…]` block.
  - `--note-table-rule` / `--note-table-guide` defined on `.paperhub-notes` (guide = `color-mix(in oklab, var(--foreground) 38%, var(--note-table-surface, var(--background)))`, re-checked in dark).
  - Header bold always; tint only under `[data-table-preset="notion"]`.
  - `border-collapse: collapse`; the existing 150 ms border transitions kept; none under reduced motion.
  - `[data-guides="off"]` hides guides.
- Test: `frontend/src/components/notes/tables.test.tsx`

**Interfaces:**
- Consumes: Task 6 (all).
- Produces: `tableBorderView` (extension). The table DOM carries `data-table-preset`. Cells carry inline border styles (tests assert on `style.borderTop` etc.).

- [ ] **Step 1: Write failing tests** (`testEditor.ts`):
  - (a) A table loaded with no `borders` draws Paper (first-row top 2 px solid, header bottom 1 px, last-row bottom 2 px, other edges dashed), and loading emits **no** ops (`diffDocuments` empty).
  - (b) Tab in the last cell of a Paper table → the new last row's bottom is 2 px and the row above's bottom is a guide. The same holds when the last row contains a `^`-covered cell.
  - (c) Insert above / below / left / right from the ⠿ menus and the "+" extend buttons → `borders` shape matches the grid, and lines follow ruling 2.
  - (d) Dragging a row reorders `v` with the row.
  - (e) Deleting a row that crosses a merged cell keeps the shape valid and the bottom rule.
  - (f) Ctrl+Z after an insert restores both rows and `borders` in one step.
  - (g) A read-only editor shows no dashed guides.
  - (h) A Notion table's header cells have the tint; a Paper table's do not.
- [ ] **Step 2: Run** `npx vitest run src/components/notes/tables.test.tsx` → FAIL.
- [ ] **Step 3: Implement.** Drop the old `data-table-style` tests in `tables.test.tsx`.
- [ ] **Step 4: Run** `tables.test.tsx`, `NoteEditor.test.tsx`, `sync.test.ts` + typecheck + lint → PASS.
- [ ] **Step 5: Commit** `feat(notes): draw each table's own lines and keep them through edits`.

### Task 8: Per-table presets, the last-used default, guides toggle; remove the global switch

**Files:**
- Modify: `frontend/src/store/notes.ts`:
  - `tableStyle` / `setTableStyle` / `TableStyle` → `tablePreset: Preset` (default `"paper"`) / `setTablePreset` and `tableGuides: boolean` (default `true`) / `setTableGuides`.
  - Both are persisted in `partialize`. `merge` ignores an unknown saved preset; a saved legacy `tableStyle` is dropped.
- Modify: `frontend/src/components/notes/NoteTableHandles.tsx` (`TableMoreMenu`: a "Table style" group with **Paper** and **Notion** radio items, each with its hint, first; then the header toggles, caption / notes, then **Hide/Show edit guides**). Choosing a preset writes `dumpBorders(presetLines(grid, depth, p))` and calls `setTablePreset(p)`.
- Modify: `frontend/src/components/notes/tableKeys.ts` (`newTableContent` callers: `insertNoteTable` and the paste path set `props.borders` from the store's `tablePreset`)
- Modify: `frontend/src/components/notes/NoteEditor.tsx` (`data-guides={tableGuides ? "on" : "off"}`)
- Modify: `frontend/src/components/notes/NotesToolbar.tsx` (remove the Table style radio group, `RadioItem` if unused, `Rows3`/`Grid3x3` imports)
- Modify: `frontend/src/components/notes/useNoteExport.ts`, `frontend/src/lib/api.ts` (`exportNote(noteId, format, tableLabel)`; drop `NoteTableStyle` and `table_style`)
- Modify: `frontend/src/components/notes/guideData.ts` (replace `tableStyle` with entries: `tablePresets` "⋯ › Paper / Notion" → "Re-draws this table; the next new table uses it"; `tableBorders` "Cell menu › Borders…"; `tableMerge` "Cell menu › Merge / Split")
- Modify: the four `notes.json`:
  - add `table.style`, `table.presetPaper`, `table.presetPaperHint`, `table.presetNotion`, `table.presetNotionHint`, `table.guidesHide`, `table.guidesShow` and the guide keys;
  - remove `toolbar.tableStyle`, `toolbar.tableThreeLine*`, `toolbar.tableGrid*`.
  - English: "Paper" / "Thick rules at the top and end, a thin rule under the header · also the next new table's default"; "Notion" / "Every line thin, the header tinted · also the next new table's default"; "Hide edit guides" / "Show edit guides".
- Test: `tables.test.tsx`, `NotesToolbar.test.tsx`, `store/notes.test.ts`, `useNoteExport.test.tsx`

**Interfaces:**
- Consumes: Tasks 6, 7.

- [ ] **Step 1: Write failing tests:**
  - The table ⋯ menu shows Paper checked on a table without `borders`. Choosing Notion re-draws only that table (a second table is unchanged) and sets `tablePreset` to `"notion"`.
  - `/table` then inserts a Notion table, and a pasted table is Notion too.
  - The preset persists across a store rehydrate. A saved `{tableStyle:"grid"}` rehydrates to `tablePreset "paper"`.
  - Hide edit guides removes the dashed guides and persists.
  - The notes ⋯ menu has no "Table style".
  - Export requests carry no `table_style`.
  - The menu opens with the popover zoom/fade and closes back into its trigger (`data-closed` present before unmount).
- [ ] **Step 2: Run** the four test files → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** them + `src/locales/parity.test.ts` + typecheck + lint → PASS.
- [ ] **Step 5: Commit** in two commits: `feat(notes): per-table Paper and Notion presets`, then `refactor(notes): drop the global table style switch`.

### Task 9: Merge and split cells; the cell menu

**Files:**
- Modify: `frontend/src/components/notes/NoteEditor.tsx` (`useCreateBlockNote` option `tables: { splitCells: true, cellBackgroundColor: false, cellTextColor: false, headers: false }`)
- Modify: `frontend/src/components/notes/tableKeys.ts`:
  - `unmerge` → `normaliseTable` keeps `colspan`/`rowspan` and still strips colours and fractional widths.
  - `needsNormalising` no longer flags spans.
  - `cleanPastedTable` keeps spans and sets `headerRows` to `headerDepth` with the header on.
  - The file comment is updated.
- Modify: `frontend/src/components/notes/NoteTableHandles.tsx`: `NoCellHandle` → `NoteTableCellHandle` (a ⌄ at the selection's top-right corner) opening `NoteTableCellMenu`:
  - **Merge cells** (enabled for a multi-cell selection that is not exactly one merged cell);
  - **Split cell** (enabled for exactly one merged cell);
  - **Borders…** (Task 10 opens it; this task renders the item);
  - Insert row above / below;
  - Align left / centre / right (the selection's columns);
  - Delete rows (the selection's rows).

  Merge/split call `tableHandles.mergeCells` / `splitCell`, then set `headerRows` to `headerDepth` when the header is on. The merged / split cells fade in over 150 ms (Web Animations, none under reduced motion).
- Modify: `frontend/src/components/notes/noteSuggestions.tsx` (`tableCellChanges` compares cells by grid anchor `(row, col)` via `tableGrid`, not by index)
- Modify: `frontend/src/lib/notes/ops.ts` (if `blockText` assumes rectangular rows) + `frontend/src/types/notes.ts` (no type change expected; confirm)
- Modify: four `notes.json` (`table.cellOptions` "Cell options", `table.merge` "Merge cells", `table.split` "Split cell", `table.borders` "Borders…", `table.bordersHint` "Pick a weight, then click an icon", `table.deleteRows` "Delete rows")
- Test: `tables.test.tsx`, `noteSuggestions.test.ts`

**Interfaces:**
- Consumes: `tableGrid`, `headerDepth` (Task 6).
- Produces: `NoteTableCellMenu` with prop `onBorders(anchor: HTMLElement, selection: Selection) => void`, wired to Task 10's picker.

- [ ] **Step 1: Write failing tests:**
  - (a) Select 1×2 cells, then Merge → one cell with `colspan: 2` holding both texts. `borders` is unchanged (same grid).
  - (b) Split restores 2 cells, and the second is empty.
  - (c) Merging a row-0 cell down with the header on → `headerRows: 2`. Splitting it → 1.
  - (d) Delete row on a row crossed by a rowspan: no throw, spans shrink.
  - (e) Pasting an HTML table with `colspan="2"` keeps the span and strips a background colour. (This replaces the old "spans → 1" assertion.)
  - (f) The cell menu offers Merge disabled on a single plain cell, Split only on a merged one, and no colour item.
  - (g) A suggestion that changes one body cell of a merged table lists exactly that cell.
  - (h) The menu opens from ⌄ with zoom/fade and Escape returns focus to ⌄.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tables.test.tsx`, `noteSuggestions.test.ts`, `parity.test.ts` + typecheck + lint → PASS.
- [ ] **Step 5: Commit** `feat(notes): merge and split table cells`.

### Task 10: The Borders… picker

**Files:**
- Create: `frontend/src/components/notes/TableBordersPicker.tsx` (a `ui/` Popover anchored on the ⌄)
- Modify: `frontend/src/components/notes/NoteTableHandles.tsx` (wire `onBorders`)
- Modify: four `notes.json` (`borders.title` "Borders · {{rows}}×{{cols}} cells", `borders.weight` "Line weight", `borders.thin` "Thin", `borders.thick` "Thick", `borders.all` "All borders", `borders.inner` "Inner borders", `borders.innerH` "Inner horizontal borders", `borders.innerV` "Inner vertical borders", `borders.outer` "Outer borders", `borders.left` "Left border", `borders.top` "Top border", `borders.right` "Right border", `borders.bottom` "Bottom border", `borders.clear` "Clear borders", `borders.unusable` "{{name}} (this selection has none of these lines)", `borders.preview` "Preview of the selection's borders", `borders.note` "Click an icon again to clear those lines. A merged cell's edge is its whole span; an edge without a border shows dashed only while editing.")
- Test: `frontend/src/components/notes/TableBordersPicker.test.tsx`

**Interfaces:**
- Consumes: `segments`, `applyBorderSet`, `isPressed`, `isUsable`, `bordersOf`, `dumpBorders` (Task 6); `onBorders` (Task 9).
- Produces: `TableBordersPicker({ editor, blockId, selection, anchor, onClose })`.

Layout per the demo:
- A live SVG preview of the selection (top-left) and the weight segmented control (細/粗 drawn as a 1 px and a 2 px line).
- A `role="toolbar"` 2×5 grid in Google Sheets order: all, inner, innerH, innerV, outer / left, top, right, bottom, clear. Each icon draws its lines solid at the chosen weight and the others dashed.
- The note line below.

- [ ] **Step 1: Write failing tests:**
  - Clicking **Inner** on a 2×3 selection sets those lines to the current weight (`borders` updated, the cells' styles change, the preview redraws), and the button reads `aria-pressed="true"`. Clicking it again clears them.
  - Switching to Thick then **Outer** makes the outer segments 2.
  - **innerH** is `aria-disabled` with the "(none of these lines)" label on a one-row selection, and clicking it does nothing.
  - A selection holding a merge has no inner line drawn inside it.
  - Arrow keys move focus across the 5-column grid with wrap. Home / End work, and Enter applies.
  - Escape closes (exit animation `data-closed`) and focuses ⌄.
  - The first usable icon gets focus on open.
  - Each click is one undo step.
- [ ] **Step 2: Run** `npx vitest run src/components/notes/TableBordersPicker.test.tsx` → FAIL.
- [ ] **Step 3: Implement.** Each click is one `updateBlock(blockId, { props: { borders } })`.
- [ ] **Step 4: Run** it + `tables.test.tsx` + `parity.test.ts` + typecheck + lint → PASS.
- [ ] **Step 5: Commit** `feat(notes): borders picker for a cell selection`.

## Phase 3 — Spec, gates, live check

### Task 11: SRS update, full gates, live check on a copy, live migration

- [ ] **Step 1: SRS.** Update the two Tables sentences to what shipped: the rulings above that the spec leaves open (borders shape, header depth, whole-cell escapes, edit-guides toggle, agent tables default to Paper, Word header-less rows). Commit alone: `docs(srs): table presets, per-line borders and merged cells as shipped`.
- [ ] **Step 2: Gates.**
  - Backend: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`.
  - Frontend: `npm test -- --run`, `npm run typecheck`, `npm run lint`, `npm run build`.

  All must be green.
- [ ] **Step 3: Live check on a workspace copy.** Copy the live workspace (`paperhub-copy-workspace`) to a scratch dir and run `paperhub-backfill-table-borders` on it. Run this branch's backend on `:8110` and Vite on `:4173` against the copy; the user's servers are not touched. In headless Chromium at pane widths 580 and 715 px, light and dark, on a note made before this branch:
  - an old table shows Paper;
  - switch it to Notion; `/table` → Notion;
  - Borders… inner / outer / thick;
  - merge a two-level header; split;
  - Tab past the last cell (thick end rule stays at the bottom);
  - delete a row crossing a merge;
  - Hide edit guides;
  - export Markdown (open `note.md`: HTML table) and Word (open: merged cells, lines as drawn).

  Screenshot each. Every listed difference between the demo and the shipped UI is resolved or named as a kept shipped behaviour.
- [ ] **Step 4: Agent turn on the copy.** `POST /chat` "把表格第三列的 GDN 改成 GDN (ours)" on the note with the merged table. Read `tool_calls` (`notes:edit`): merges and borders kept, one cell changed. Then "在筆記裡加一個比較 A 和 B 的表格" → the new table has no `borders` and draws Paper.
- [ ] **Step 5: Fix everything found** (TDD + commit each), then write `.superpowers/table-borders-merge-report.md` (checks, screenshots, fixes, migration counts).
- [ ] **Step 6: Live migration (with the user's explicit approval).** Back up `D:\GitHub\PaperHub\backend\workspace\paperhub.db`. Show the user the dry-run counts on the live DB, then run `paperhub-backfill-table-borders` for real, and run again to show `set 0`.
- [ ] **Step 7: Review demo.** After the live check, set `features/table-presets`, `features/table-borders`, `features/table-merge` → `shipped` (note what shipped) plus a `changes/<feature>-v<N>` line each, per demo-review-port step 8.
