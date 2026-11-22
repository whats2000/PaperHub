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
import { Plugin, PluginKey, type EditorState, type Transaction } from "@tiptap/pm/state";
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
import { anchorAt, isAnchor, tableGrid, type GridCell } from "./tableGrid";

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

/** Counts the tables drawn (tests check a change re-draws only its own). */
export const tableBorderStats = { decorated: 0 };

/** One table's decorations: its preset, and each cell's edge weights. */
function decorateTable(node: PMNode, pos: number): Decoration[] {
  tableBorderStats.decorated++;
  const content = contentOf(node);
  const borders = bordersOf({ borders: node.attrs.borders as unknown }, content);
  const decos = [Decoration.node(pos, pos + node.nodeSize, { "data-table-preset": borders.preset })];
  const { grid } = tableGrid(content);
  const w = (x: Weight) => String(x);
  let rowPos = pos + 1;
  node.forEach((row, _o, r) => {
    // This row's own cells by their index in the row.
    const own = new Map<number, GridCell>();
    for (const g of grid[r] ?? []) if (isAnchor(g) && g.row === r) own.set(g.index, g);
    let cellPos = rowPos + 1;
    let index = 0;
    row.forEach((cell) => {
      const at = own.get(index);
      if (at) {
        const e = cellEdges(borders, at);
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
  return decos;
}

function decorate(doc: PMNode): DecorationSet {
  const decos: Decoration[] = [];
  doc.descendants((node, pos) => {
    if (node.type.name !== "table") return node.type.name !== "tableRow";
    decos.push(...decorateTable(node, pos));
    return false;
  });
  return DecorationSet.create(doc, decos);
}

/** The decorations after a change: the old ones mapped, and only the
 * tables the change touched drawn again. */
function redecorate(prev: DecorationSet, tr: Transaction, oldDoc: PMNode, doc: PMNode): DecorationSet {
  const range = changedRange(oldDoc, doc);
  let set = prev.map(tr.mapping, doc);
  if (!range) return set;
  for (const { pos, node } of tablesIn(doc, range.from, range.to)) {
    const end = pos + node.nodeSize;
    // Only this table's own (find also returns ones touching its edges).
    set = set.remove(set.find(pos, end).filter((d) => d.from >= pos && d.to <= end));
    set = set.add(doc, decorateTable(node, pos));
  }
  return set;
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

const children = (n: PMNode) => {
  const out: PMNode[] = [];
  n.forEach((c) => out.push(c));
  return out;
};

/** Which items of the longer list pair with the shorter list's, in order,
 * for the highest total `score` (ties: the earlier pairing). */
function bestPairs(short: number, long: number, score: (s: number, l: number) => number): number[] {
  // f[i][j]: best score pairing short[i..] with long[j..].
  const f = Array.from({ length: short + 1 }, () => new Array<number>(long + 1).fill(0));
  for (let i = short - 1; i >= 0; i--)
    for (let j = long - (short - i); j >= 0; j--) {
      const take = score(i, j) + f[i + 1]![j + 1]!;
      const skip = long - j > short - i ? f[i]![j + 1]! : -Infinity;
      f[i]![j] = Math.max(take, skip);
    }
  const out: number[] = [];
  let j = 0;
  for (let i = 0; i < short; i++) {
    while (long - j > short - i && f[i]![j]! !== score(i, j) + f[i + 1]![j + 1]!) j++;
    out.push(j++);
  }
  return out;
}

/** The ops of an edit script. Within a run of changes, deletions and
 * insertions pair up as items edited in place (kept): the pairs whose old
 * and new items share the most cells (deleting a merged cell's own row
 * moves the cell into the next row: that next row is the one edited). A
 * lone deletion and a lone insertion of equal items is a move. */
function opsOf<T>(
  steps: Step[],
  a: T[],
  b: T[],
  eq: (x: T, y: T) => boolean,
  kinds: { ins: "insertRow" | "insertCol"; del: "deleteRow" | "deleteCol"; move: "moveRow" | "moveCol" },
  shared: (x: T, y: T) => number,
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
    const d: number[] = [];
    const n: number[] = [];
    while (k < steps.length && steps[k]!.kind !== "keep") {
      const s = steps[k]!;
      if (s.kind === "del") d.push(s.old);
      else if (s.kind === "ins") n.push(s.new);
      k++;
    }
    // Pair the run's deletions and insertions: [old index in d, new index in n].
    const pairs: [number, number][] =
      d.length >= n.length
        ? bestPairs(n.length, d.length, (i, j) => shared(a[d[j]!]!, b[n[i]!]!)).map((j, i) => [j, i])
        : bestPairs(d.length, n.length, (i, j) => shared(a[d[i]!]!, b[n[j]!]!)).map((j, i) => [i, j]);
    let di = 0;
    let ni = 0;
    for (const [pd, pn] of [...pairs, [d.length, n.length] as [number, number]]) {
      for (; di < pd; di++) ops.push({ kind: kinds.del, at });
      for (; ni < pn; ni++) ops.push({ kind: kinds.ins, at: at++ });
      if (pd < d.length) {
        at++; // edited in place
        di++;
        ni++;
      }
    }
  }
  return ops;
}

/** How many cells two rows share (equal nodes, each used once). */
function sharedCells(x: PMNode, y: PMNode): number {
  const left = children(y);
  let n = 0;
  x.forEach((cell) => {
    const i = left.findIndex((c) => c.eq(cell));
    if (i >= 0) {
      left.splice(i, 1);
      n++;
    }
  });
  return n;
}

/** How many positions two columns share. */
const sharedPositions = (x: PMNode[], y: PMNode[]) => x.filter((n, i) => y[i] !== undefined && n.eq(y[i])).length;

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
    if (steps) ops = opsOf(steps, a, c, (x, y) => x.eq(y), { ins: "insertRow", del: "deleteRow", move: "moveRow" }, sharedCells);
  } else if (was.rows === now.rows && was.cols !== now.cols) {
    const a = columns(oldTable);
    const c = columns(table);
    const steps = align(a, c, sameColumn);
    if (steps) ops = opsOf(steps, a, c, sameColumn, { ins: "insertCol", del: "deleteCol", move: "moveCol" }, sharedPositions);
  } else if (was.rows === now.rows && was.cols === now.cols) {
    // Same shape: a dragged row or column moves its lines.
    const a = children(oldTable);
    const c = children(table);
    const steps = align(a, c, (x, y) => x.eq(y));
    const rowOps = steps ? opsOf(steps, a, c, (x, y) => x.eq(y), { ins: "insertRow", del: "deleteRow", move: "moveRow" }, sharedCells) : [];
    if (rowOps.length === 1 && rowOps[0]!.kind === "moveRow") ops = rowOps;
    else {
      const ca = columns(oldTable);
      const cc = columns(table);
      const cs = align(ca, cc, sameColumn);
      const colOps = cs ? opsOf(cs, ca, cc, sameColumn, { ins: "insertCol", del: "deleteCol", move: "moveCol" }, sharedPositions) : [];
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
        apply: (tr, prev, old, state) => (tr.docChanged ? redecorate(prev, tr, old.doc, state.doc) : prev),
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
