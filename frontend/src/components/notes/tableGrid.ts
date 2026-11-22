/** A table on its grid: merged cells counted by what they cover. Mirrors
 * the backend's `models.table_grid` / `header_depth`. */
import type { TableCell, TableContent } from "@/types/notes";

/** A real cell placed on the grid: its top-left `row` / `col`, the grid
 * rows / columns it covers and its position in `rows[row].cells`. */
export type GridCell = { row: number; col: number; rowspan: number; colspan: number; index: number };

/** `grid[r][c]`: the cell that starts there, or `[row, col]` of the cell
 * that covers it. */
export type Grid = (GridCell | [number, number])[][];

function span(cell: TableCell, key: "rowspan" | "colspan"): number {
  if (typeof cell !== "object" || Array.isArray(cell) || cell.type !== "tableCell") return 1;
  const v = cell.props?.[key];
  return typeof v === "number" && Number.isInteger(v) && v >= 1 ? v : 1;
}

/** The grid of a table (each cell at the first free column of its row).
 * Malformed spans are clipped to the table's edge, never thrown: the
 * editor only draws, the backend validates. */
export function tableGrid(c: TableContent): { grid: Grid; rows: number; cols: number } {
  const n = c.rows.length;
  const taken = new Map<string, GridCell | [number, number]>();
  const key = (r: number, col: number) => `${r},${col}`;
  let width = 0;
  c.rows.forEach((row, r) => {
    let col = 0;
    row.cells.forEach((cell, index) => {
      while (taken.has(key(r, col))) col++;
      const rs = Math.min(span(cell, "rowspan"), n - r);
      const cs = span(cell, "colspan");
      const anchor: GridCell = { row: r, col, rowspan: rs, colspan: cs, index };
      for (let dr = 0; dr < rs; dr++)
        for (let dc = 0; dc < cs; dc++) {
          const k = key(r + dr, col + dc);
          if (!taken.has(k)) taken.set(k, dr === 0 && dc === 0 ? anchor : [r, col]);
        }
      col += cs;
    });
    while (taken.has(key(r, col))) col++;
    width = Math.max(width, col);
  });
  const grid: Grid = Array.from({ length: n }, (_, r) =>
    Array.from({ length: width }, (_, col) => taken.get(key(r, col)) ?? [r, col]),
  );
  return { grid, rows: n, cols: width };
}

export const isAnchor = (g: GridCell | [number, number]): g is GridCell => !Array.isArray(g);

/** The anchor cell that covers grid position `(r, c)`. */
export function anchorAt(grid: Grid, r: number, c: number): GridCell | null {
  const at = grid[r]?.[c];
  if (!at) return null;
  if (isAnchor(at)) return at;
  const a = grid[at[0]]?.[at[1]];
  return a && isAnchor(a) ? a : null;
}

/** How many grid rows the header covers: 0 without one, else row 0 plus
 * every row a row-0 cell spans into. */
export function headerDepth(c: TableContent): number {
  if (!c.headerRows) return 0;
  const { grid, rows } = tableGrid(c);
  if (!rows) return 0;
  let depth = 1;
  for (const at of grid[0] ?? []) if (isAnchor(at)) depth = Math.max(depth, at.rowspan);
  return depth;
}
