/** Tables the Notion way, where BlockNote's own table falls short.
 *
 * - `/table` inserts a 3 × 3 table whose first row is a header
 *   (`insertNoteTable`); BlockNote's inserts 3 × 2 with none.
 * - Tab in the last cell adds a row (`tableKeys`); BlockNote's Tab stops there.
 * - A table keeps only what the notes store, whatever brought it in
 *   (`tableClean`): merged cells yes, cell colours no, whole-pixel widths; a
 *   pasted one also gets a header row and its column alignment
 *   (`notePasteHandler`).
 */
import {
  contentNodeToTableContent,
  type InlineContentSchema,
  type StyleSchema,
  createExtension,
  getNodeById,
  insertOrUpdateBlockForSlashMenu,
  tableContentToNodes,
} from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, TextSelection } from "@tiptap/pm/state";
import { TableMap, removeColumn, removeRow } from "@tiptap/pm/tables";
import type { EditorView } from "@tiptap/pm/view";

import { useNotesStore } from "@/store/notes";
import type { TableCell, TableContent } from "@/types/notes";

import { dumpBorders, presetLines, tableShape } from "./tableBorders";
import { headerDepth, isAnchor, tableGrid } from "./tableGrid";

export type Alignment = "left" | "center" | "right";

/** What these helpers use of a BlockNote editor. */
interface TableEditor {
  document: unknown[];
  getTextCursorPosition: () => { block: { id: string; type: string; content?: unknown } };
  updateBlock: (block: string, update: never) => unknown;
  _tiptapEditor: { view: EditorView };
}

type FullCell = { type: "tableCell"; props: Record<string, unknown>; content: Record<string, unknown>[] | string };

function asCell(cell: TableCell): FullCell {
  if (typeof cell === "object" && !Array.isArray(cell) && cell.type === "tableCell") {
    return { type: "tableCell", props: cell.props ?? {}, content: cell.content };
  }
  return { type: "tableCell", props: {}, content: cell as FullCell["content"] };
}

const emptyCell = (textAlignment?: unknown): FullCell => ({
  type: "tableCell",
  props: typeof textAlignment === "string" && textAlignment !== "left" ? { textAlignment } : {},
  content: [],
});

/** A new table: `rows` × `cols` empty cells, the first row a header. */
export function newTableContent(rows = 3, cols = 3): TableContent {
  return {
    type: "tableContent",
    columnWidths: Array.from({ length: cols }, () => null),
    headerRows: 1,
    rows: Array.from({ length: rows }, () => ({ cells: Array.from({ length: cols }, () => emptyCell()) })),
  };
}

/** The viewer's default preset's lines for a table (`/table`, a paste). */
function defaultBorders(content: TableContent): string {
  const s = tableShape(content);
  return dumpBorders(presetLines(s.rows, s.cols, s.headerRows, useNotesStore.getState().tablePreset));
}

/** `/table`: a 3 × 3 table with a header row in the viewer's last preset,
 * the caret in its first cell. */
export function insertNoteTable(editor: unknown) {
  const content = newTableContent();
  insertOrUpdateBlockForSlashMenu(editor as never, {
    type: "table",
    props: { borders: defaultBorders(content) },
    content,
  } as never);
}

/** Run styles a table cell keeps: everything but colours. */
function stripRuns<T>(content: T): T {
  if (!Array.isArray(content)) return content;
  return content.map((run: unknown) => {
    if (!run || typeof run !== "object") return run;
    const r = run as { styles?: Record<string, unknown>; content?: unknown };
    let out = r as Record<string, unknown>;
    if (r.styles && ("textColor" in r.styles || "backgroundColor" in r.styles)) {
      const styles = { ...r.styles };
      delete styles.textColor;
      delete styles.backgroundColor;
      out = { ...out, styles };
    }
    // A link holds its own runs.
    if (Array.isArray(r.content)) out = { ...out, content: stripRuns(r.content) };
    return out;
  }) as T;
}

/** A table's cells as the notes keep them: merged cells kept (`colspan` /
 * `rowspan` and `textAlignment` are the only props), runs without colours,
 * and a row that does not reach the table's width filled with empty cells. */
function cleanCells(content: TableContent): { rows: FullCell[][]; width: number } {
  const { grid, cols } = tableGrid(content);
  const width = Math.max(1, cols);
  const rows = content.rows.map((row, r) => {
    const cells: FullCell[] = row.cells.map((raw) => {
      const cell = asCell(raw);
      const props: Record<string, unknown> = {};
      for (const key of ["rowspan", "colspan"] as const) {
        const v = Number(cell.props[key] ?? 1);
        if (Number.isInteger(v) && v > 1) props[key] = v;
      }
      const align = cell.props.textAlignment;
      if (typeof align === "string" && align !== "left") props.textAlignment = align;
      return { type: "tableCell", props, content: stripRuns(cell.content) };
    });
    // Grid places this row covers (its own cells and spans from above).
    const line = grid[r] ?? [];
    let covered = 0;
    for (let c = 0; c < width; c++) {
      const at = line[c];
      if (at && (isAnchor(at) || at[0] !== r || at[1] !== c)) covered = c + 1;
    }
    for (let c = covered; c < width; c++) cells.push(emptyCell());
    return cells;
  });
  return { rows, width };
}

/** A table as the notes keep it: merged cells kept, colours stripped,
 * column widths whole pixels, header rows as deep as row 0's spans, a
 * header column 0 or 1. */
function normaliseTable(content: TableContent): TableContent {
  const { rows, width } = cleanCells(content);
  const out: TableContent = {
    type: "tableContent",
    columnWidths: Array.from({ length: width }, (_, i) => {
      const w = content.columnWidths?.[i];
      return typeof w === "number" ? Math.round(w) : null;
    }),
    rows: rows.map((cells) => ({ cells })),
  };
  if (content.headerRows) out.headerRows = headerDepth({ ...out, headerRows: 1 });
  if (content.headerCols) out.headerCols = 1;
  return out;
}

/** A pasted table as the notes keep it (`normaliseTable`), with a header
 * row when it had none. */
export function cleanPastedTable(content: TableContent): TableContent {
  const out = normaliseTable(content);
  return { ...out, headerRows: headerDepth({ ...out, headerRows: 1 }) };
}

/** Each table block of a document (nested ones too). */
function tables(blocks: unknown[], into: { id: string; content: TableContent }[] = []) {
  for (const b of blocks as { id: string; type: string; content?: unknown; children?: unknown[] }[]) {
    if (b.type === "table") into.push({ id: b.id, content: b.content as TableContent });
    tables(b.children ?? [], into);
  }
  return into;
}

const COLOUR_MARKS = new Set(["textColor", "backgroundColor"]);

/** True when a table node holds what the notes do not keep: a coloured
 * cell or run, a fractional column width. */
function needsNormalising(table: PMNode): boolean {
  let bad = false;
  table.descendants((node) => {
    if (bad) return false;
    const a = node.attrs as Record<string, unknown>;
    if (node.type.name === "tableCell" || node.type.name === "tableHeader") {
      const colour = (v: unknown) => v !== undefined && v !== null && v !== "default";
      const widths = Array.isArray(a.colwidth) ? (a.colwidth as unknown[]) : [];
      bad =
        colour(a.backgroundColor) ||
        colour(a.textColor) ||
        widths.some((w) => typeof w === "number" && !Number.isInteger(w));
    } else if (node.isText) {
      bad = node.marks.some((m) => COLOUR_MARKS.has(m.type.name));
    }
    return !bad;
  });
  return bad;
}

/** Where `next` differs from `prev`, in `next` (and in `prev`); null when equal. */
export function changedRange(
  prev: PMNode,
  next: PMNode,
): { from: number; to: number; prevFrom: number; prevTo: number } | null {
  const from = next.content.findDiffStart(prev.content);
  if (from === null || from === undefined) return null;
  const end = next.content.findDiffEnd(prev.content) ?? { a: next.content.size, b: prev.content.size };
  let to = end.a;
  let prevTo = end.b;
  const overlap = from - Math.min(to, prevTo);
  if (overlap > 0) {
    to += overlap;
    prevTo += overlap;
  }
  return { from, to: Math.max(to, from), prevFrom: from, prevTo: Math.max(prevTo, from) };
}

/** Table nodes overlapping [from, to] of `doc`, with their positions. */
export function tablesIn(doc: PMNode, from: number, to: number): { pos: number; node: PMNode }[] {
  const out: { pos: number; node: PMNode }[] = [];
  const end = Math.min(doc.content.size, Math.max(to, from + 1));
  doc.nodesBetween(Math.min(from, end), end, (node, pos) => {
    if (node.type.name === "table") {
      out.push({ pos, node });
      return false;
    }
    return true;
  });
  return out;
}

/** Every change that leaves a table holding what the notes do not keep (a
 * drop, a paste, an inserted block, a column drag) is followed by its
 * normalised form, so no path can save a coloured cell or a fractional
 * width. */
export const tableClean = createExtension(({ editor }) => ({
  key: "tableClean",
  prosemirrorPlugins: [
    new Plugin({
      appendTransaction(trs, oldState, state) {
        if (!trs.some((t) => t.docChanged)) return null;
        const range = changedRange(oldState.doc, state.doc);
        if (!range) return null;
        const bad = tablesIn(state.doc, range.from, range.to).filter((t) => needsNormalising(t.node));
        if (!bad.length) return null;
        const schema = editor.schema as { inlineContentSchema: InlineContentSchema; styleSchema: StyleSchema };
        const tr = state.tr;
        for (const { pos, node } of bad.reverse()) {
          const content = contentNodeToTableContent(node, schema.inlineContentSchema, schema.styleSchema);
          const rows = tableContentToNodes(
            normaliseTable(content as unknown as TableContent) as never,
            state.schema,
            schema.styleSchema,
          );
          tr.replaceWith(pos + 1, pos + node.nodeSize - 1, rows);
        }
        return tr;
      },
    }),
  ],
}))();

const ALIGN = new Set(["left", "center", "right"]);

const DELIMITER_ROW = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

/** Column alignments of each GFM table in Markdown text: a delimiter row
 * right under a header row. */
function markdownAlignments(text: string): (Alignment | null)[][] {
  const out: (Alignment | null)[][] = [];
  const lines = text.split(/\r?\n/);
  lines.forEach((line, i) => {
    const header = lines[i - 1];
    if (!line.includes("|") || !DELIMITER_ROW.test(line) || !header?.includes("|") || DELIMITER_ROW.test(header)) return;
    const cells = line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|");
    out.push(
      cells.map((c) => {
        const t = c.trim();
        const l = t.startsWith(":");
        const r = t.endsWith(":");
        return l && r ? "center" : r ? "right" : l ? "left" : null;
      }),
    );
  });
  return out;
}

/** Column alignments of each table in pasted HTML (`align` or
 * `text-align` of its first row's cells; BlockNote drops `align`, and a
 * Markdown table reaches it as HTML with `align`). */
function htmlAlignments(html: string): (Alignment | null)[][] {
  const doc = new DOMParser().parseFromString(html, "text/html");
  return [...doc.querySelectorAll("table")].map((table) => {
    const row = table.querySelector("tr");
    const out: (Alignment | null)[] = [];
    for (const cell of row ? [...row.children] : []) {
      const el = cell as HTMLElement;
      const a = (el.getAttribute("align") || el.style.textAlign || "").toLowerCase();
      const span = Math.max(1, Number(el.getAttribute("colspan") ?? 1) || 1);
      for (let i = 0; i < span; i++) out.push(ALIGN.has(a) ? (a as Alignment) : null);
    }
    return out;
  });
}

/** The alignment sources a paste may have used, in BlockNote's order of
 * preference: Markdown, then plain text read as Markdown, then HTML. */
function alignmentSources(event: unknown): (Alignment | null)[][][] {
  const data = (event as { clipboardData?: DataTransfer | null } | undefined)?.clipboardData;
  if (!data) return [];
  const types = [...(data.types ?? [])];
  const get = (t: string) => (types.includes(t) ? data.getData(t) : "");
  const out: (Alignment | null)[][][] = [];
  if (get("text/markdown")) out.push(markdownAlignments(get("text/markdown")));
  if (get("text/plain")) out.push(markdownAlignments(get("text/plain")));
  if (get("text/html")) out.push(htmlAlignments(get("text/html")));
  return out;
}

/** The first source whose tables match the pasted ones one to one (same
 * number of tables, same column counts), or null: then no alignment is
 * guessed. */
export function pickAlignments(
  pasted: TableContent[],
  sources: (Alignment | null)[][][],
): (Alignment | null)[][] | null {
  const cols = pasted.map((t) => t.rows[0]?.cells.length ?? 0);
  return (
    sources.find((s) => s.length === pasted.length && s.every((aligns, i) => aligns.length === cols[i])) ?? null
  );
}

/** A table's columns given the pasted alignment where its cells have none. */
function withAlignments(content: TableContent, aligns: (Alignment | null)[] | undefined): TableContent {
  if (!aligns?.some((a) => a && a !== "left")) return content;
  return {
    ...content,
    rows: content.rows.map((row) => ({
      cells: row.cells.map((raw, i) => {
        const want = aligns[i];
        const cell = asCell(raw);
        const have = cell.props.textAlignment;
        if (!want || want === "left" || (have !== undefined && have !== "left")) return raw;
        return { ...cell, props: { ...cell.props, textAlignment: want } };
      }),
    })),
  };
}

/** The notes editor's paste: BlockNote's own (HTML, Markdown text, a GFM
 * table as text; `tableClean` normalises what it brings), then each table
 * the paste brought in gets a header row and its columns' alignment, which
 * BlockNote drops. Tables already in the note are left alone. */
export function notePasteHandler(context: {
  event?: unknown;
  editor: unknown;
  defaultPasteHandler: () => boolean | undefined;
}): boolean | undefined {
  const editor = context.editor as TableEditor;
  const before = new Set(tables(editor.document).map((t) => t.id));
  const sources = alignmentSources(context.event);
  const handled = context.defaultPasteHandler();
  const added = tables(editor.document).filter((t) => !before.has(t.id));
  const aligns = pickAlignments(
    added.map((t) => t.content),
    sources,
  );
  added.forEach((t, i) => {
    const content = cleanPastedTable(withAlignments(t.content, aligns?.[i]));
    editor.updateBlock(t.id, { type: "table", props: { borders: defaultBorders(content) }, content } as never);
  });
  return handled;
}

/** The caret's cell: its row and column and the table's size, or null. */
function caretCell(view: EditorView): { row: number; col: number; rows: number; cols: number } | null {
  const { $from } = view.state.selection;
  for (let d = $from.depth; d > 0; d--) {
    if ($from.node(d).type.name !== "tableRow") continue;
    const table = $from.node(d - 1);
    return { row: $from.index(d - 1), col: $from.index(d), rows: table.childCount, cols: $from.node(d).childCount };
  }
  return null;
}

/** Tab in a table's last cell: a new row below (each cell keeping its
 * column's alignment) and the caret in its first cell. False elsewhere. */
export function addRowAfterLastCell(editor: unknown): boolean {
  const e = editor as TableEditor;
  const view = e._tiptapEditor.view;
  if (!view.state.selection.empty) return false;
  const at = caretCell(view);
  if (!at || at.row !== at.rows - 1 || at.col !== at.cols - 1) return false;
  const block = e.getTextCursorPosition().block;
  if (block.type !== "table") return false;
  const content = block.content as TableContent;
  // One empty cell per grid column (a merged cell in the last row covers
  // several), each with the alignment of the cell above it.
  const { grid, cols } = tableGrid(content);
  const lastLine = grid[grid.length - 1] ?? [];
  const row = {
    cells: Array.from({ length: cols }, (_, c) => {
      const at = lastLine[c];
      const a = at && isAnchor(at) ? at : at ? grid[at[0]]?.[at[1]] : undefined;
      const cell = a && isAnchor(a) ? content.rows[a.row]?.cells[a.index] : undefined;
      return emptyCell(cell === undefined ? undefined : asCell(cell).props.textAlignment);
    }),
  };
  e.updateBlock(block.id, { type: "table", content: { ...content, rows: [...content.rows, row] } } as never);
  const info = getNodeById(block.id, view.state.doc);
  if (!info) return true;
  const table = info.node.firstChild!;
  // Into the block container and the table, past every row but the new one,
  // then into its row, its first cell and that cell's paragraph.
  let pos = info.posBeforeNode + 2;
  for (let i = 0; i < table.childCount - 1; i++) pos += table.child(i).nodeSize;
  view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, pos + 3)).scrollIntoView());
  return true;
}

/** Sets the alignment of every cell of column `col` (GFM `:--` `:-:` `--:`). */
export function setColumnAlignment(editor: unknown, blockId: string, col: number, align: Alignment) {
  const e = editor as { getBlock: (id: string) => { content?: unknown } | undefined } & TableEditor;
  const content = e.getBlock(blockId)?.content as TableContent | undefined;
  if (!content) return;
  const rows = content.rows.map((row) => ({
    cells: row.cells.map((raw, i) => {
      if (i !== col) return raw;
      const cell = asCell(raw);
      return { ...cell, props: { ...cell.props, textAlignment: align } };
    }),
  }));
  e.updateBlock(blockId, { type: "table", content: { ...content, rows } } as never);
}

/** The alignment every cell of column `col` shares, or null when they differ. */
export function columnAlignment(content: TableContent, col: number): Alignment | null {
  const all = new Set(
    content.rows.map((row) => {
      const cell = row.cells[col];
      const a = cell === undefined ? "left" : asCell(cell).props.textAlignment;
      return typeof a === "string" ? a : "left";
    }),
  );
  return all.size === 1 ? ([...all][0] as Alignment) : null;
}

/** Tab in the last cell adds a row (ahead of BlockNote's Tab, which stops
 * there). Shift+Tab and Tab elsewhere are BlockNote's: the next / previous cell. */
export const tableKeys = createExtension({
  key: "tableKeys",
  keyboardShortcuts: {
    Tab: ({ editor }) => addRowAfterLastCell(editor),
  },
});

/** Deletes row or column `index` of table `blockId`; the last one left takes
 * the table with it. */
export function deleteRowOrColumn(editor: unknown, blockId: string, orientation: "row" | "column", index: number) {
  const e = editor as {
    getBlock: (id: string) => { content?: unknown } | undefined;
    removeBlocks: (ids: string[]) => unknown;
  } & TableEditor;
  const content = e.getBlock(blockId)?.content as TableContent | undefined;
  if (!content) return;
  const { cols } = tableGrid(content);
  if ((orientation === "row" ? content.rows.length : cols) <= 1) {
    e.removeBlocks([blockId]);
    return;
  }
  const merged = content.rows.some((r) => r.cells.some((c) => {
    const p = asCell(c).props;
    return Number(p.colspan ?? 1) > 1 || Number(p.rowspan ?? 1) > 1;
  }));
  if (merged) {
    // prosemirror-tables shrinks the spans that cross the row or column.
    deleteAcrossSpans(e._tiptapEditor.view, blockId, orientation, index);
    return;
  }
  const next: TableContent =
    orientation === "row"
      ? { ...content, rows: content.rows.filter((_, i) => i !== index) }
      : {
          ...content,
          columnWidths: content.columnWidths.filter((_, i) => i !== index),
          rows: content.rows.map((row) => ({ cells: row.cells.filter((_, i) => i !== index) })),
        };
  e.updateBlock(blockId, { type: "table", content: next } as never);
}

/** Deletes grid row or column `index` of a table with merged cells. */
function deleteAcrossSpans(view: EditorView, blockId: string, orientation: "row" | "column", index: number) {
  const info = getNodeById(blockId, view.state.doc);
  const table = info?.node.firstChild;
  if (!info || !table || table.type.name !== "table") return;
  const tablePos = info.posBeforeNode + 1;
  // Exactly that grid row / column (a selection would grow to the spans
  // crossing it and delete them all); the spans crossing it shrink.
  const rect = { map: TableMap.get(table), table, tableStart: tablePos + 1, left: 0, top: 0, right: 0, bottom: 0 };
  const tr = view.state.tr;
  if (orientation === "row") removeRow(tr, rect, index);
  else removeColumn(tr, rect, index);
  view.dispatch(tr);
}
