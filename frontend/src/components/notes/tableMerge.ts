/** Merging and splitting a table's cells, and the cell menu's other
 * commands, on prosemirror-tables (which keeps spans right when rows and
 * columns are inserted or deleted).
 *
 * After a merge or a split the table's header row covers every row a row-0
 * cell spans into (`headerDepth`), as the backend stores it. A merge that
 * would leave a row with no cell of its own (a whole row's width over
 * several rows) is refused: the editor's table row needs one.
 */
import { TextSelection, type EditorState } from "@tiptap/pm/state";
import {
  CellSelection,
  TableMap,
  addRowAfter,
  addRowBefore,
  cellAround,
  deleteRow,
  mergeCells,
  splitCell,
} from "@tiptap/pm/tables";
import type { EditorView } from "@tiptap/pm/view";
import type { Node as PMNode } from "@tiptap/pm/model";

import { prefersReducedMotion } from "@/lib/motion";
import type { TableContent } from "@/types/notes";

import type { Alignment } from "./tableKeys";
import type { Selection } from "./tableBorders";
import { headerDepth } from "./tableGrid";

interface MergeEditor {
  _tiptapEditor: { view: EditorView };
  getBlock: (id: string) => { type: string; content?: unknown } | undefined;
  updateBlock: (id: string, update: never) => unknown;
  removeBlocks: (ids: string[]) => unknown;
}

/** The table around the selection: its node, position, block id and the
 * selected grid rectangle (a caret's cell when no cells are selected). */
export function tableSelection(
  state: EditorState,
): { table: PMNode; tablePos: number; blockId: string; rect: Selection; cells: number } | null {
  const sel = state.selection;
  let $cell = sel instanceof CellSelection ? sel.$anchorCell : null;
  let $other = sel instanceof CellSelection ? sel.$headCell : null;
  if (!$cell) {
    const $from = sel.$from;
    for (let d = $from.depth; d > 0; d--) {
      const n = $from.node(d);
      if (n.type.name === "tableCell" || n.type.name === "tableHeader") {
        $cell = state.doc.resolve($from.before(d));
        $other = $cell;
        break;
      }
    }
  }
  if (!$cell || !$other) return null;
  const tableDepth = $cell.depth - 1;
  const table = $cell.node(tableDepth);
  if (table.type.name !== "table") return null;
  const tablePos = $cell.before(tableDepth);
  const start = tablePos + 1;
  const map = TableMap.get(table);
  const r = map.rectBetween($cell.pos - start, $other.pos - start);
  const blockId = String(state.doc.resolve(tablePos).parent.attrs.id);
  return {
    table,
    tablePos,
    blockId,
    rect: { r0: r.top, r1: r.bottom - 1, c0: r.left, c1: r.right - 1 },
    cells: map.cellsInRect(r).length,
  };
}

/** Selects the grid rectangle from cell `a` to cell `b` ([row, column]) of
 * the first table around the selection, or of the document's first table. */
export function selectCells(editor: unknown, a: [number, number], b: [number, number]) {
  const view = (editor as MergeEditor)._tiptapEditor.view;
  let tablePos = -1;
  view.state.doc.descendants((node, pos) => {
    if (tablePos >= 0) return false;
    if (node.type.name === "table") tablePos = pos;
    return tablePos < 0;
  });
  if (tablePos < 0) return;
  const table = view.state.doc.nodeAt(tablePos)!;
  const map = TableMap.get(table);
  const start = tablePos + 1;
  const at = (rc: [number, number]) => view.state.doc.resolve(start + map.map[rc[0] * map.width + rc[1]]!);
  view.dispatch(view.state.tr.setSelection(new CellSelection(at(a), at(b))));
}

/** Whether the selection is several cells that are not exactly one merged
 * cell and leave every row a cell of its own: a row below the selection's
 * first must keep a cell that starts in it outside the selected columns. */
export function canMerge(state: EditorState): boolean {
  const t = tableSelection(state);
  if (!t || !(state.selection instanceof CellSelection) || t.cells < 2) return false;
  const map = TableMap.get(t.table);
  for (let r = t.rect.r0 + 1; r <= t.rect.r1; r++) {
    let own = false;
    for (let c = 0; c < map.width && !own; c++) {
      if (c >= t.rect.c0 && c <= t.rect.c1) continue;
      own = map.findCell(map.map[r * map.width + c]!).top === r;
    }
    if (!own) return false;
  }
  return true;
}

/** Whether the selection is exactly one merged cell. */
export function canSplit(state: EditorState): boolean {
  const t = tableSelection(state);
  if (!t || t.cells !== 1) return false;
  return t.rect.r1 > t.rect.r0 || t.rect.c1 > t.rect.c0;
}

/** The header row, when on, covers every row a row-0 cell spans into. */
function fixHeader(editor: MergeEditor, blockId: string) {
  const block = editor.getBlock(blockId);
  if (block?.type !== "table") return;
  const content = block.content as TableContent;
  if (!content.headerRows) return;
  const depth = headerDepth({ ...content, headerRows: 1 });
  if (depth !== content.headerRows) {
    editor.updateBlock(blockId, { type: "table", content: { ...content, headerRows: depth } } as never);
  }
}

function run(editor: MergeEditor, cmd: (state: EditorState, dispatch: EditorView["dispatch"]) => boolean): boolean {
  const view = editor._tiptapEditor.view;
  return cmd(view.state, (tr) => view.dispatch(tr));
}

/** The selected cells fade in (150 ms) where a merge or split redrew
 * them; nothing moves under reduced motion. */
function fadeSelected(view: EditorView) {
  if (prefersReducedMotion()) return;
  const sel = view.state.selection;
  const cells: HTMLElement[] = [];
  const add = (pos: number) => {
    const dom = view.nodeDOM(pos);
    if (dom instanceof HTMLElement && typeof dom.animate === "function") cells.push(dom);
  };
  if (sel instanceof CellSelection) sel.forEachCell((_n, pos) => add(pos));
  else {
    const t = tableSelection(view.state);
    if (t) {
      const map = TableMap.get(t.table);
      for (const rel of new Set(map.cellsInRect({ left: t.rect.c0, top: t.rect.r0, right: t.rect.c1 + 1, bottom: t.rect.r1 + 1 })))
        add(t.tablePos + 1 + rel);
    }
  }
  for (const el of cells) el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 150, easing: "ease-out" });
}

/** Merges the selected cells: their texts go into the first, each on a
 * line of its own (`joinCellParagraphs`, in `tableClean`). */
export function mergeSelected(editor: unknown): boolean {
  const e = editor as MergeEditor;
  const state = e._tiptapEditor.view.state;
  const t = tableSelection(state);
  if (!t || !canMerge(state)) return false;
  if (!run(e, mergeCells)) return false;
  fixHeader(e, t.blockId);
  fadeSelected(e._tiptapEditor.view);
  return true;
}

/** Splits the selected merged cell (the others come back empty). */
export function splitSelected(editor: unknown): boolean {
  const e = editor as MergeEditor;
  const state = e._tiptapEditor.view.state;
  const t = tableSelection(state);
  if (!t || !canSplit(state)) return false;
  if (!run(e, splitCell)) return false;
  fixHeader(e, t.blockId);
  fadeSelected(e._tiptapEditor.view);
  return true;
}

/** Deletes the selected rows (spans crossing them shrink); every row:
 * the table goes. */
export function deleteSelectedRows(editor: unknown): boolean {
  const e = editor as MergeEditor;
  const t = tableSelection(e._tiptapEditor.view.state);
  if (!t) return false;
  if (t.rect.r0 === 0 && t.rect.r1 === TableMap.get(t.table).height - 1) {
    e.removeBlocks([t.blockId]);
    return true;
  }
  if (!run(e, deleteRow)) return false;
  fixHeader(e, t.blockId);
  return true;
}

/** A row above or below the selection. */
export function insertRow(editor: unknown, side: "above" | "below"): boolean {
  return run(editor as MergeEditor, side === "above" ? addRowBefore : addRowAfter);
}

/** Aligns the selection's columns: every cell starting in one of them. */
export function alignSelectedColumns(editor: unknown, align: Alignment): void {
  const e = editor as MergeEditor;
  const view = e._tiptapEditor.view;
  const t = tableSelection(view.state);
  if (!t) return;
  const map = TableMap.get(t.table);
  const start = t.tablePos + 1;
  const tr = view.state.tr;
  const seen = new Set<number>();
  for (let r = 0; r < map.height; r++) {
    for (let c = t.rect.c0; c <= t.rect.c1; c++) {
      const rel = map.map[r * map.width + c]!;
      if (seen.has(rel)) continue;
      seen.add(rel);
      if (map.findCell(rel).left !== c) continue;
      const node = t.table.nodeAt(rel);
      if (node) tr.setNodeMarkup(start + rel, undefined, { ...node.attrs, textAlignment: align });
    }
  }
  if (tr.docChanged) view.dispatch(tr);
}

/** The selected cells (each once, a merged one at its top left) with their
 * document position, sorted by row then column. */
function selectedCells(state: EditorState): { row: number; col: number; pos: number; node: PMNode }[] {
  const t = tableSelection(state);
  if (!t) return [];
  const map = TableMap.get(t.table);
  const rels = new Set(map.cellsInRect({ left: t.rect.c0, top: t.rect.r0, right: t.rect.c1 + 1, bottom: t.rect.r1 + 1 }));
  const out: { row: number; col: number; pos: number; node: PMNode }[] = [];
  for (const rel of rels) {
    const node = t.table.nodeAt(rel);
    if (!node) continue;
    const { top, left } = map.findCell(rel);
    out.push({ row: top, col: left, pos: t.tablePos + 1 + rel, node });
  }
  return out.sort((a, b) => a.row - b.row || a.col - b.col);
}

function isEmptyCell(node: PMNode): boolean {
  const empty = node.type.createAndFill(node.attrs);
  return empty ? node.content.eq(empty.content) : node.textContent === "";
}

/** A selected cell that holds something: its grid row and column. */
export interface FilledCell {
  row: number;
  col: number;
}

/** The selected cells that hold something (Clear contents is off when
 * there are none). */
export function filledCells(state: EditorState): FilledCell[] {
  return selectedCells(state)
    .filter((c) => !isEmptyCell(c.node))
    .map(({ row, col }) => ({ row, col }));
}

/** What Clear contents took: the table and each cell's content, for Undo. */
export interface ClearedCells {
  blockId: string;
  cells: (FilledCell & { content: PMNode["content"] })[];
}

/** Empties the selected cells (one undo step); what they held, for the
 * toast's Undo. Nothing to clear: null. */
export function clearCells(editor: unknown): ClearedCells | null {
  const view = (editor as MergeEditor)._tiptapEditor.view;
  const t = tableSelection(view.state);
  const full = selectedCells(view.state).filter((c) => !isEmptyCell(c.node));
  if (!t || !full.length) return null;
  const tr = view.state.tr;
  for (const c of [...full].reverse()) {
    const empty = c.node.type.createAndFill(c.node.attrs);
    if (empty) tr.replaceWith(c.pos + 1, c.pos + c.node.nodeSize - 1, empty.content);
  }
  // The selection stays on the cleared cells: a cell selection as it was,
  // a caret at the start of its (now empty) cell, not mapped past it.
  const sel = view.state.selection;
  if (sel instanceof CellSelection) {
    const anchor = tr.mapping.map(sel.$anchorCell.pos, -1);
    const head = tr.mapping.map(sel.$headCell.pos, -1);
    tr.setSelection(new CellSelection(tr.doc.resolve(anchor), tr.doc.resolve(head)));
  } else {
    const $cell = cellAround(sel.$from);
    if ($cell) tr.setSelection(TextSelection.near(tr.doc.resolve(tr.mapping.map($cell.pos, -1) + 1)));
  }
  view.dispatch(tr);
  return { blockId: t.blockId, cells: full.map(({ row, col, node }) => ({ row, col, content: node.content })) };
}

/** Puts cleared text back, into the cells still empty (one typed into since
 * keeps what it has); a cell gone, or no longer starting there, is skipped. */
export function restoreCells(editor: unknown, cleared: ClearedCells): void {
  const view = (editor as MergeEditor)._tiptapEditor.view;
  let tablePos = -1;
  view.state.doc.descendants((node, pos) => {
    if (tablePos >= 0) return false;
    if (node.type.name === "blockContainer" && String(node.attrs.id) === cleared.blockId) {
      if (node.firstChild?.type.name === "table") tablePos = pos + 1;
      return false;
    }
    return true;
  });
  if (tablePos < 0) return;
  const table = view.state.doc.nodeAt(tablePos)!;
  const map = TableMap.get(table);
  const tr = view.state.tr;
  const back = cleared.cells
    .map((c) => {
      if (c.row >= map.height || c.col >= map.width) return null;
      const rel = map.map[c.row * map.width + c.col]!;
      const at = map.findCell(rel);
      const node = table.nodeAt(rel);
      if (at.top !== c.row || at.left !== c.col || !node || !isEmptyCell(node)) return null;
      return { pos: tablePos + 1 + rel, node, content: c.content };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    .sort((a, b) => b.pos - a.pos);
  for (const c of back) tr.replaceWith(c.pos + 1, c.pos + c.node.nodeSize - 1, c.content);
  if (tr.docChanged) view.dispatch(tr);
}

/** The alignment shared by every cell starting in the selected columns;
 * null when they differ (the menu then checks none). */
export function selectedAlignment(state: EditorState): Alignment | null {
  const t = tableSelection(state);
  if (!t) return null;
  const map = TableMap.get(t.table);
  const seen = new Set<string>();
  const done = new Set<number>();
  for (let r = 0; r < map.height; r++) {
    for (let c = t.rect.c0; c <= t.rect.c1; c++) {
      const rel = map.map[r * map.width + c]!;
      if (done.has(rel)) continue;
      done.add(rel);
      if (map.findCell(rel).left !== c) continue;
      const attrs: Record<string, unknown> = t.table.nodeAt(rel)?.attrs ?? {};
      const a = attrs.textAlignment;
      seen.add(typeof a === "string" ? a : "left");
    }
  }
  return seen.size === 1 ? ([...seen][0] as Alignment) : null;
}
