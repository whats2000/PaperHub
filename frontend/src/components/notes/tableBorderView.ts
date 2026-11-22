/** Each table drawn with its own lines (`tableBorders.ts`), and the lines
 * kept through structure changes.
 *
 * - Every cell gets its edges' weights as `data-bt` / `data-br` / `data-bb`
 *   / `data-bl` (0 none, 1 thin, 2 thick; a merged cell's side is the
 *   heaviest segment it covers), which index.css draws. An edge without a
 *   line is a dashed edit guide while the editor is editable and the guides
 *   are on (`data-guides` on the editor root); read-only, nothing.
 * - The table carries `data-table-preset` (its CSS tints Notion's header).
 * - A change that leaves a table whose lines no longer fit its grid (a row
 *   or column inserted, deleted or dragged) rewrites its `borders` in the
 *   same transaction, so one undo takes back both. Rows and columns are
 *   aligned old → new by their content (LCS over `Node.eq`; ties put an
 *   insertion as late as possible) and replayed by `refit`. A table without
 *   lines is left alone: it draws as Paper whatever its shape.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey, type EditorState } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

import type { TableContent } from "@/types/notes";

import { changedRange, tablesIn } from "./tableKeys";
import {
  type Borders,
  type Shape,
  type StructureOp,
  type Weight,
  bordersOf,
  cellEdges,
  dumpBorders,
  parseBorders,
  refit,
  sameBorders,
  shapeOf,
  tableShape,
} from "./tableBorders";
import { anchorAt, isAnchor, tableGrid } from "./tableGrid";

const KEY = new PluginKey("tableBorderView");

const isCell = (n: PMNode) => n.type.name === "tableCell" || n.type.name === "tableHeader";

/** A table node's shape as `tableContent` (spans and header rows only:
 * enough for its grid). */
function contentOf(table: PMNode): TableContent {
  const rows: TableContent["rows"] = [];
  let headerRows = 0;
  let counting = true;
  table.forEach((row) => {
    const cells: TableContent["rows"][number]["cells"] = [];
    let allHeader = row.childCount > 0;
    row.forEach((cell) => {
      if (!isCell(cell)) return;
      if (cell.type.name !== "tableHeader") allHeader = false;
      const a = cell.attrs as { colspan?: number; rowspan?: number };
      cells.push({ type: "tableCell", props: { colspan: a.colspan ?? 1, rowspan: a.rowspan ?? 1 }, content: [] });
    });
    if (counting && allHeader) headerRows++;
    else counting = false;
    rows.push({ cells });
  });
  return { type: "tableContent", columnWidths: [], headerRows, rows };
}

function decorate(doc: PMNode): DecorationSet {
  const decos: Decoration[] = [];
  doc.descendants((node, pos) => {
    if (node.type.name !== "table") return node.type.name !== "tableRow";
    const content = contentOf(node);
    const borders = bordersOf({ borders: node.attrs.borders as unknown }, content);
    decos.push(Decoration.node(pos, pos + node.nodeSize, { "data-table-preset": borders.preset }));
    const { grid } = tableGrid(content);
    let rowPos = pos + 1;
    node.forEach((row, _o, r) => {
      let cellPos = rowPos + 1;
      let index = 0;
      row.forEach((cell) => {
        const at = grid[r]?.find((g) => isAnchor(g) && g.row === r && g.index === index);
        if (at && isAnchor(at)) {
          const e = cellEdges(borders, at);
          const w = (x: Weight) => String(x);
          decos.push(
            Decoration.node(cellPos, cellPos + cell.nodeSize, {
              "data-bt": w(e.top),
              "data-br": w(e.right),
              "data-bb": w(e.bottom),
              "data-bl": w(e.left),
            }),
          );
        }
        cellPos += cell.nodeSize;
        index++;
      });
      rowPos += row.nodeSize;
    });
    return false;
  });
  return DecorationSet.create(doc, decos);
}

// ------------------------------------------------------------ structure

type Step = { kind: "keep" } | { kind: "del"; old: number } | { kind: "ins"; new: number };

/** The edit script turning `a` into `b` (LCS; a match is taken as early as
 * possible, so an insertion lands as late as possible). */
function align<T>(a: T[], b: T[], eq: (x: T, y: T) => boolean): Step[] | null {
  if (a.length * b.length > 40000) return null;
  const n = a.length;
  const m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i]![j] = eq(a[i]!, b[j]!) ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!);
  const out: Step[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && eq(a[i]!, b[j]!) && dp[i]![j] === dp[i + 1]![j + 1]! + 1) {
      out.push({ kind: "keep" });
      i++;
      j++;
    } else if (i < n && (j >= m || dp[i + 1]![j]! >= dp[i]![j + 1]!)) {
      out.push({ kind: "del", old: i++ });
    } else {
      out.push({ kind: "ins", new: j++ });
    }
  }
  return out;
}

/** The ops of an edit script: within a run of changes, deletions and
 * insertions pair up as items edited in place (kept); a lone deletion and a
 * lone insertion of equal items is a move. */
function opsOf<T>(
  steps: Step[],
  a: T[],
  b: T[],
  eq: (x: T, y: T) => boolean,
  kinds: { ins: "insertRow" | "insertCol"; del: "deleteRow" | "deleteCol"; move: "moveRow" | "moveCol" },
): StructureOp[] {
  const dels = steps.filter((s) => s.kind === "del") as { kind: "del"; old: number }[];
  const ins = steps.filter((s) => s.kind === "ins") as { kind: "ins"; new: number }[];
  if (dels.length === 1 && ins.length === 1 && eq(a[dels[0]!.old]!, b[ins[0]!.new]!)) {
    return [{ kind: kinds.move, from: dels[0]!.old, to: ins[0]!.new }];
  }
  const ops: StructureOp[] = [];
  let at = 0;
  let k = 0;
  while (k < steps.length) {
    if (steps[k]!.kind === "keep") {
      at++;
      k++;
      continue;
    }
    // A run of changes between two kept items: its first deletions and
    // insertions pair up as items edited in place (a row whose merge
    // shrank), the rest are deleted or inserted after them.
    let dels = 0;
    let ins = 0;
    while (k < steps.length && steps[k]!.kind !== "keep") {
      if (steps[k]!.kind === "del") dels++;
      else ins++;
      k++;
    }
    const edited = Math.min(dels, ins);
    at += edited;
    for (let i = edited; i < dels; i++) ops.push({ kind: kinds.del, at });
    for (let i = edited; i < ins; i++) ops.push({ kind: kinds.ins, at: at++ });
  }
  return ops;
}

const children = (n: PMNode) => {
  const out: PMNode[] = [];
  n.forEach((c) => out.push(c));
  return out;
};

/** Each grid column's cells, top to bottom (a merged cell in each column it
 * covers). */
function columns(table: PMNode): PMNode[][] {
  const rows = children(table).map(children);
  const { grid, cols } = tableGrid(contentOf(table));
  return Array.from({ length: cols }, (_, c) =>
    grid.flatMap((_, r) => {
      const a = anchorAt(grid, r, c);
      const cell = a ? rows[a.row]?.[a.index] : undefined;
      return cell ? [cell] : [];
    }),
  );
}

const sameColumn = (x: PMNode[], y: PMNode[]) => x.length === y.length && x.every((n, i) => n.eq(y[i]!));

/** The lines `table` should have after `old` became it. */
export function fitLines(oldTable: PMNode, table: PMNode, b: Borders): Borders | null {
  const oldContent = contentOf(oldTable);
  const content = contentOf(table);
  const was: Shape = tableShape(oldContent);
  const now: Shape = tableShape(content);
  const lines = shapeOf(b);
  if (lines.rows !== was.rows || lines.cols !== was.cols) {
    // Lines that did not fit before: the preset's for the new shape.
    return refit(b, { ...was, rows: lines.rows, cols: lines.cols }, now, []);
  }
  let ops: StructureOp[] = [];
  if (was.cols === now.cols && was.rows !== now.rows) {
    const a = children(oldTable);
    const c = children(table);
    const steps = align(a, c, (x, y) => x.eq(y));
    if (steps) ops = opsOf(steps, a, c, (x, y) => x.eq(y), { ins: "insertRow", del: "deleteRow", move: "moveRow" });
  } else if (was.rows === now.rows && was.cols !== now.cols) {
    const a = columns(oldTable);
    const c = columns(table);
    const steps = align(a, c, sameColumn);
    if (steps) ops = opsOf(steps, a, c, sameColumn, { ins: "insertCol", del: "deleteCol", move: "moveCol" });
  } else if (was.rows === now.rows && was.cols === now.cols) {
    // Same shape: a dragged row or column moves its lines.
    const a = children(oldTable);
    const c = children(table);
    const steps = align(a, c, (x, y) => x.eq(y));
    const rowOps = steps ? opsOf(steps, a, c, (x, y) => x.eq(y), { ins: "insertRow", del: "deleteRow", move: "moveRow" }) : [];
    if (rowOps.length === 1 && rowOps[0]!.kind === "moveRow") ops = rowOps;
    else {
      const ca = columns(oldTable);
      const cc = columns(table);
      const cs = align(ca, cc, sameColumn);
      const colOps = cs ? opsOf(cs, ca, cc, sameColumn, { ins: "insertCol", del: "deleteCol", move: "moveCol" }) : [];
      if (colOps.length === 1 && colOps[0]!.kind === "moveCol") ops = colOps;
      else return null; // an edit inside cells: the lines stay
    }
  }
  return refit(b, was, now, ops);
}

/** Old table nodes by block id within the changed range. */
function tablesById(doc: PMNode, from: number, to: number): Map<string, PMNode> {
  const out = new Map<string, PMNode>();
  for (const { pos, node } of tablesIn(doc, from, to)) out.set(String(doc.resolve(pos).parent.attrs.id), node);
  return out;
}

function refitTables(oldState: EditorState, state: EditorState) {
  const range = changedRange(oldState.doc, state.doc);
  if (!range) return null;
  const before = tablesById(oldState.doc, range.prevFrom, range.prevTo);
  let tr: ReturnType<EditorState["tr"]["setNodeAttribute"]> | null = null;
  for (const { pos, node } of tablesIn(state.doc, range.from, range.to)) {
    const b = parseBorders(node.attrs.borders);
    if (!b) continue; // no lines: drawn as Paper whatever its shape
    const old = before.get(String(state.doc.resolve(pos).parent.attrs.id));
    if (!old || old.eq(node)) continue;
    const fitted = fitLines(old, node, b);
    if (!fitted || sameBorders(fitted, b)) continue;
    tr = (tr ?? state.tr).setNodeAttribute(pos, "borders", dumpBorders(fitted));
  }
  return tr;
}

export const tableBorderView = createExtension({
  key: "tableBorderView",
  prosemirrorPlugins: [
    new Plugin<DecorationSet>({
      key: KEY,
      state: {
        init: (_c, state) => decorate(state.doc),
        apply: (tr, prev, _old, state) => (tr.docChanged ? decorate(state.doc) : prev),
      },
      appendTransaction(trs, oldState, state) {
        if (!trs.some((t) => t.docChanged)) return null;
        return refitTables(oldState, state);
      },
      props: {
        decorations: (state) => KEY.getState(state) as DecorationSet | undefined,
      },
    }),
  ],
});
