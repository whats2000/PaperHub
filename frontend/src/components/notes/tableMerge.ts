/** Merging and splitting a table's cells, and the cell menu's other
 * commands, on prosemirror-tables (which keeps spans right when rows and
 * columns are inserted or deleted).
 *
 * After a merge or a split the table's header row covers every row a row-0
 * cell spans into (`headerDepth`), as the backend stores it. A merge that
 * would leave a row with no cell of its own (a whole row's width over
 * several rows) is refused: the editor's table row needs one.
 */
import type { EditorState } from "@tiptap/pm/state";
import {
  CellSelection,
  TableMap,
  addRowAfter,
  addRowBefore,
  deleteRow,
  mergeCells,
  splitCell,
} from "@tiptap/pm/tables";
import type { EditorView } from "@tiptap/pm/view";
import type { Node as PMNode } from "@tiptap/pm/model";

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
 * cell and leave every row a cell of its own. */
export function canMerge(state: EditorState): boolean {
  const t = tableSelection(state);
  if (!t || !(state.selection instanceof CellSelection) || t.cells < 2) return false;
  const map = TableMap.get(t.table);
  const fullWidth = t.rect.c0 === 0 && t.rect.c1 === map.width - 1;
  return !(fullWidth && t.rect.r1 > t.rect.r0);
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

/** Merges the selected cells (their texts joined into the first). */
export function mergeSelected(editor: unknown): boolean {
  const e = editor as MergeEditor;
  const state = e._tiptapEditor.view.state;
  const t = tableSelection(state);
  if (!t || !canMerge(state)) return false;
  if (!run(e, mergeCells)) return false;
  fixHeader(e, t.blockId);
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
