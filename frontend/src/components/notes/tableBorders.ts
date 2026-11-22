/** A table's own lines: every line between cells is a border of its own.
 *
 * Stored as the table block's `borders` prop, a JSON string
 * `{"preset","h","v"}` over the table's grid (R rows × C columns): `h` has
 * R+1 strings of C digits (`h[r][c]` the line above grid row `r` in column
 * `c`, `h[R]` the bottom rule), `v` has R strings of C+1 digits (`v[r][c]`
 * the line left of grid column `c` in row `r`). A digit is 0 none, 1 thin,
 * 2 thick. A missing or empty prop is the Paper preset's lines. Mirrors the
 * backend's `notes/table_borders.py`; the structure rules follow the plan's
 * ruling 2 (H5b.1).
 */
import type { TableContent } from "@/types/notes";

import { type GridCell, headerDepth, isAnchor, tableGrid } from "./tableGrid";

export type Weight = 0 | 1 | 2;
export type Preset = "paper" | "notion";
export const PRESETS: readonly Preset[] = ["paper", "notion"];

export interface Borders {
  preset: Preset;
  h: string[];
  v: string[];
}

export type Shape = { rows: number; cols: number; headerRows: number };

const DIGITS = /^[012]*$/;

/** The lines a `borders` prop holds, or null when it is missing, empty or
 * malformed (the backend refuses malformed ones). */
export function parseBorders(raw: unknown): Borders | null {
  if (typeof raw !== "string" || !raw) return null;
  let obj: unknown;
  try {
    obj = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!obj || typeof obj !== "object") return null;
  const { preset, h, v } = obj as Record<string, unknown>;
  if (preset !== "paper" && preset !== "notion") return null;
  const lines = (x: unknown): x is string[] =>
    Array.isArray(x) && x.every((l) => typeof l === "string" && DIGITS.test(l));
  if (!lines(h) || !lines(v) || h.length !== v.length + 1) return null;
  const cols = h[0]?.length ?? 0;
  if (!cols || h.some((l) => l.length !== cols) || v.some((l) => l.length !== cols + 1)) return null;
  return { preset, h: [...h], v: [...v] };
}

/** The prop's compact JSON (keys in the order preset, h, v), the same
 * string the backend's `dump_borders` writes. */
export function dumpBorders(b: Borders): string {
  return JSON.stringify({ preset: b.preset, h: b.h, v: b.v });
}

/** A preset's lines for a grid shape. Paper: a thick top rule, a thin rule
 * under the header (when it has one and a body), a thick end rule. Notion:
 * every line thin. */
export function presetLines(rows: number, cols: number, headerRows: number, preset: Preset): Borders {
  if (preset === "notion") {
    return {
      preset,
      h: Array.from({ length: rows + 1 }, () => "1".repeat(cols)),
      v: Array.from({ length: rows }, () => "1".repeat(cols + 1)),
    };
  }
  const h = Array.from({ length: rows + 1 }, () => "0".repeat(cols));
  if (headerRows > 0 && headerRows < rows) h[headerRows] = "1".repeat(cols);
  h[0] = "2".repeat(cols);
  h[rows] = "2".repeat(cols);
  return { preset, h, v: Array.from({ length: rows }, () => "0".repeat(cols + 1)) };
}

export function shapeOf(b: Borders): { rows: number; cols: number } {
  return { rows: b.v.length, cols: b.h[0]?.length ?? 0 };
}

export function tableShape(c: TableContent): Shape {
  const { rows, cols } = tableGrid(c);
  return { rows, cols, headerRows: headerDepth(c) };
}

/** The table's lines: its own when they fit its grid, else its preset's
 * (Paper when it has none) for its shape. */
export function bordersOf(props: Record<string, unknown>, c: TableContent): Borders {
  const s = tableShape(c);
  const b = parseBorders(props.borders);
  if (b) {
    const got = shapeOf(b);
    if (got.rows === s.rows && got.cols === s.cols) return b;
  }
  return presetLines(s.rows, s.cols, s.headerRows, b?.preset ?? "paper");
}

export const sameBorders = (a: Borders, b: Borders) =>
  a.preset === b.preset && a.h.join("|") === b.h.join("|") && a.v.join("|") === b.v.join("|");

// ---------------------------------------------------------------- structure

const bodyLine = (preset: Preset) => (preset === "notion" ? "1" : "0");
const spliceStr = (s: string, at: number, del: number, ins = "") => s.slice(0, at) + ins + s.slice(at + del);

/** The line the new row (or column) at boundary `at` gets on its far side:
 * a body-interior neighbour's, else the preset's body line. */
function neighbour<T>(lines: T[], at: number, count: number, head: number, body: () => T): T {
  if (head < at && at < count) return lines[at] ?? body();
  if (at === head && at + 1 < count) return lines[at + 1] ?? body();
  if (at === count && count - 1 > head) return lines[count - 1] ?? body();
  return body();
}

/** A row inserted at boundary `at` (0 ≤ at ≤ R; it becomes row `at`). The
 * line at `at` stays above the new row (the end rule stays at the bottom);
 * its vertical lines copy the row above it (below it at the top). */
export function insertRow(b: Borders, at: number, headerRows: number): Borders {
  const { rows, cols } = shapeOf(b);
  const x = neighbour(b.h, at, rows, headerRows, () => bodyLine(b.preset).repeat(cols));
  const h = [...b.h];
  if (at >= rows) h.splice(rows, 0, x);
  else h.splice(at + 1, 0, x);
  const v = [...b.v];
  v.splice(at, 0, b.v[Math.max(0, at - 1)] ?? bodyLine(b.preset).repeat(cols + 1));
  return { preset: b.preset, h, v };
}

/** Row `r` removed with the line below it (the last row: the line above
 * it, so the end rule stays). */
export function deleteRow(b: Borders, r: number): Borders {
  const { rows } = shapeOf(b);
  const h = [...b.h];
  h.splice(r === rows - 1 ? r : r + 1, 1);
  const v = [...b.v];
  v.splice(r, 1);
  return { preset: b.preset, h, v };
}

/** Row `from` moved to index `to`: its vertical lines travel with it; the
 * horizontal rules mark positions and stay. */
export function moveRow(b: Borders, from: number, to: number): Borders {
  const v = [...b.v];
  const [row] = v.splice(from, 1);
  if (row !== undefined) v.splice(to, 0, row);
  return { preset: b.preset, h: [...b.h], v };
}

/** A column inserted at boundary `at` (0 ≤ at ≤ C). */
export function insertCol(b: Borders, at: number): Borders {
  const { cols } = shapeOf(b);
  const h = b.h.map((l) => spliceStr(l, at, 0, l[Math.max(0, at - 1)] ?? bodyLine(b.preset)));
  const v = b.v.map((l) => {
    const x = neighbour([...l], at, cols, 0, () => bodyLine(b.preset));
    return at >= cols ? spliceStr(l, cols, 0, x) : spliceStr(l, at + 1, 0, x);
  });
  return { preset: b.preset, h, v };
}

/** Column `c` removed with the line right of it (the last column: left). */
export function deleteCol(b: Borders, c: number): Borders {
  const { cols } = shapeOf(b);
  return {
    preset: b.preset,
    h: b.h.map((l) => spliceStr(l, c, 1)),
    v: b.v.map((l) => spliceStr(l, c === cols - 1 ? c : c + 1, 1)),
  };
}

/** Column `from` moved to index `to`: its horizontal lines travel with it. */
export function moveCol(b: Borders, from: number, to: number): Borders {
  const move = (l: string) => {
    const d = [...l];
    const [x] = d.splice(from, 1);
    if (x !== undefined) d.splice(to, 0, x);
    return d.join("");
  };
  return { preset: b.preset, h: b.h.map(move), v: [...b.v] };
}

export type StructureOp =
  | { kind: "insertRow"; at: number }
  | { kind: "deleteRow"; at: number }
  | { kind: "moveRow"; from: number; to: number }
  | { kind: "insertCol"; at: number }
  | { kind: "deleteCol"; at: number }
  | { kind: "moveCol"; from: number; to: number };

/** The lines after a structure change. Lines that are the preset's for the
 * old shape become the preset's for the new one; otherwise the ops are
 * replayed, and if they do not reach the new shape the preset's lines are
 * used. */
export function refit(old: Borders, oldShape: Shape, newShape: Shape, ops: StructureOp[]): Borders {
  const fresh = () => presetLines(newShape.rows, newShape.cols, newShape.headerRows, old.preset);
  if (sameBorders(old, presetLines(oldShape.rows, oldShape.cols, oldShape.headerRows, old.preset))) return fresh();
  let b = old;
  for (const op of ops) {
    const { rows, cols } = shapeOf(b);
    switch (op.kind) {
      case "insertRow":
        b = insertRow(b, Math.min(op.at, rows), oldShape.headerRows);
        break;
      case "deleteRow":
        if (rows > 1) b = deleteRow(b, Math.min(op.at, rows - 1));
        break;
      case "moveRow":
        b = moveRow(b, op.from, op.to);
        break;
      case "insertCol":
        b = insertCol(b, Math.min(op.at, cols));
        break;
      case "deleteCol":
        if (cols > 1) b = deleteCol(b, Math.min(op.at, cols - 1));
        break;
      case "moveCol":
        b = moveCol(b, op.from, op.to);
        break;
    }
  }
  const got = shapeOf(b);
  return got.rows === newShape.rows && got.cols === newShape.cols ? b : fresh();
}

// ------------------------------------------------------- cells and selections

const weight = (d: string | undefined): Weight => (d === "2" ? 2 : d === "1" ? 1 : 0);

/** Each side of a (merged) cell: the heaviest segment it covers. */
export function cellEdges(b: Borders, a: GridCell): { top: Weight; bottom: Weight; left: Weight; right: Weight } {
  const max = (ds: (string | undefined)[]) => Math.max(0, ...ds.map(weight)) as Weight;
  const across = (line: string | undefined) =>
    max(Array.from({ length: a.colspan }, (_, i) => line?.[a.col + i]));
  const down = (c: number) => max(Array.from({ length: a.rowspan }, (_, i) => b.v[a.row + i]?.[c]));
  return {
    top: across(b.h[a.row]),
    bottom: across(b.h[a.row + a.rowspan]),
    left: down(a.col),
    right: down(a.col + a.colspan),
  };
}

export type Selection = { r0: number; r1: number; c0: number; c1: number };

/** The rectangle between two grid positions, grown until no merged cell
 * crosses its edge. */
export function normaliseSelection(c: TableContent, a: [number, number], b: [number, number]): Selection {
  const { grid } = tableGrid(c);
  let r0 = Math.min(a[0], b[0]);
  let r1 = Math.max(a[0], b[0]);
  let c0 = Math.min(a[1], b[1]);
  let c1 = Math.max(a[1], b[1]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const row of grid)
      for (const at of row) {
        if (!isAnchor(at)) continue;
        const er = at.row + at.rowspan - 1;
        const ec = at.col + at.colspan - 1;
        if (at.row <= r1 && er >= r0 && at.col <= c1 && ec >= c0) {
          if (at.row < r0) [r0, changed] = [at.row, true];
          if (er > r1) [r1, changed] = [er, true];
          if (at.col < c0) [c0, changed] = [at.col, true];
          if (ec > c1) [c1, changed] = [ec, true];
        }
      }
  }
  return { r0, r1, c0, c1 };
}

export type BorderSet = "all" | "inner" | "innerH" | "innerV" | "outer" | "left" | "top" | "right" | "bottom" | "clear";
type Part = "top" | "bottom" | "left" | "right" | "innerH" | "innerV";

export const BORDER_SETS: readonly BorderSet[] = [
  "all", "inner", "innerH", "innerV", "outer", "left", "top", "right", "bottom", "clear",
];

export const SET_PARTS: Record<BorderSet, Part[]> = {
  all: ["top", "bottom", "left", "right", "innerH", "innerV"],
  inner: ["innerH", "innerV"],
  innerH: ["innerH"],
  innerV: ["innerV"],
  outer: ["top", "bottom", "left", "right"],
  left: ["left"],
  top: ["top"],
  right: ["right"],
  bottom: ["bottom"],
  clear: ["top", "bottom", "left", "right", "innerH", "innerV"],
};

export type Segment = { axis: "h" | "v"; r: number; c: number };

/** The segments a border set covers on a selection; lines inside a merged
 * cell are skipped. */
export function segments(_b: Borders, c: TableContent, s: Selection, set: BorderSet): Segment[] {
  const { grid } = tableGrid(c);
  const anchors = grid.flat().filter(isAnchor);
  // The line above row r in column col lies inside a cell spanning rows.
  const coveredH = (r: number, col: number) =>
    anchors.some((a) => a.rowspan > 1 && r > a.row && r < a.row + a.rowspan && col >= a.col && col < a.col + a.colspan);
  const coveredV = (r: number, col: number) =>
    anchors.some((a) => a.colspan > 1 && col > a.col && col < a.col + a.colspan && r >= a.row && r < a.row + a.rowspan);
  const parts: Record<Part, Segment[]> = { top: [], bottom: [], left: [], right: [], innerH: [], innerV: [] };
  for (let col = s.c0; col <= s.c1; col++) {
    parts.top.push({ axis: "h", r: s.r0, c: col });
    parts.bottom.push({ axis: "h", r: s.r1 + 1, c: col });
    for (let r = s.r0 + 1; r <= s.r1; r++) if (!coveredH(r, col)) parts.innerH.push({ axis: "h", r, c: col });
  }
  for (let r = s.r0; r <= s.r1; r++) {
    parts.left.push({ axis: "v", r, c: s.c0 });
    parts.right.push({ axis: "v", r, c: s.c1 + 1 });
    for (let col = s.c0 + 1; col <= s.c1; col++) if (!coveredV(r, col)) parts.innerV.push({ axis: "v", r, c: col });
  }
  return SET_PARTS[set].flatMap((p) => parts[p]);
}

const at = (b: Borders, g: Segment) => (g.axis === "h" ? b.h : b.v)[g.r]?.[g.c];

/** Whether the set's lines are all already `w` (its icon reads pressed). */
export function isPressed(b: Borders, c: TableContent, s: Selection, set: BorderSet, w: 1 | 2): boolean {
  if (set === "clear") return false;
  const segs = segments(b, c, s, set);
  return segs.length > 0 && segs.every((g) => at(b, g) === String(w));
}

/** Whether the selection has any of the set's lines. */
export function isUsable(b: Borders, c: TableContent, s: Selection, set: BorderSet): boolean {
  return segments(b, c, s, set).length > 0;
}

/** The set applied at weight `w`: its lines become `w`, or none when they
 * already all are (a second click clears them); `clear` sets none. */
export function applyBorderSet(b: Borders, c: TableContent, s: Selection, set: BorderSet, w: 1 | 2): Borders {
  const segs = segments(b, c, s, set);
  const value = set === "clear" || isPressed(b, c, s, set, w) ? "0" : String(w);
  const h = [...b.h];
  const v = [...b.v];
  for (const g of segs) {
    const lines = g.axis === "h" ? h : v;
    if (lines[g.r] === undefined) continue;
    lines[g.r] = spliceStr(lines[g.r] ?? "", g.c, 1, value);
  }
  return { preset: b.preset, h, v };
}
