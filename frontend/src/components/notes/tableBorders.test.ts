import { describe, expect, it } from "vitest";

import type { TableContent } from "@/types/notes";

import {
  applyBorderSet,
  bordersOf,
  cellEdges,
  deleteCol,
  deleteRow,
  dumpBorders,
  insertCol,
  insertRow,
  isPressed,
  isUsable,
  moveCol,
  moveRow,
  normaliseSelection,
  parseBorders,
  presetLines,
  refit,
  segments,
  type Borders,
} from "./tableBorders";
import { headerDepth, tableGrid } from "./tableGrid";

const cell = (text: string, props: Record<string, unknown> = {}) => ({
  type: "tableCell" as const,
  props,
  content: text ? [{ type: "text", text, styles: {} }] : [],
});

function plain(rows: number, cols: number, headerRows = 1): TableContent {
  return {
    type: "tableContent",
    columnWidths: Array.from({ length: cols }, () => null),
    headerRows,
    rows: Array.from({ length: rows }, (_, r) => ({
      cells: Array.from({ length: cols }, (_, c) => cell(`r${r}c${c}`)),
    })),
  };
}

/** The demo's two-level header: Method spans 2 rows, the group 2 columns. */
const MERGED: TableContent = {
  type: "tableContent",
  columnWidths: [null, null, null],
  headerRows: 2,
  rows: [
    { cells: [cell("Method", { rowspan: 2 }), cell("成功恢復", { colspan: 2 })] },
    { cells: [cell("Roof"), cell("Tire")] },
    { cells: [cell("GDN"), cell("18"), cell("17")] },
    { cells: [cell("TTT"), cell("15"), cell("14")] },
    { cells: [cell("LoRA"), cell("9"), cell("8")] },
  ],
};

const col = (b: Borders) => b.h.map((l) => Number(l[0]));

describe("tableGrid / headerDepth (mirrors the backend)", () => {
  it("places a merged table on its grid", () => {
    const { grid, rows, cols } = tableGrid(MERGED);
    expect([rows, cols]).toEqual([5, 3]);
    expect(grid[1]?.[0]).toEqual([0, 0]);
    expect(grid[0]?.[2]).toEqual([0, 1]);
    expect(grid[0]?.[0]).toEqual({ row: 0, col: 0, rowspan: 2, colspan: 1, index: 0 });
  });

  it("counts the rows the header covers", () => {
    expect(headerDepth({ ...MERGED, headerRows: 1 })).toBe(2);
    expect(headerDepth({ ...MERGED, headerRows: 0 })).toBe(0);
    expect(headerDepth(plain(3, 2))).toBe(1);
  });
});

describe("presets and the prop", () => {
  it("draws Paper and Notion like the backend", () => {
    const p = presetLines(5, 3, 1, "paper");
    expect(p.h).toEqual(["222", "111", "000", "000", "000", "222"]);
    expect(p.v).toEqual(Array(5).fill("0000"));
    expect(presetLines(5, 3, 2, "paper").h[2]).toBe("111");
    const n = presetLines(5, 3, 1, "notion");
    expect(new Set(n.h)).toEqual(new Set(["111"]));
    expect(new Set(n.v)).toEqual(new Set(["1111"]));
    expect(presetLines(3, 2, 0, "paper").h).toEqual(["22", "00", "00", "22"]);
  });

  it("dumps the same JSON as the backend and parses it back", () => {
    const b = presetLines(3, 3, 1, "paper");
    const raw = dumpBorders(b);
    expect(raw).toBe('{"preset":"paper","h":["222","111","000","222"],"v":["0000","0000","0000"]}');
    expect(parseBorders(raw)).toEqual(b);
    expect(parseBorders("")).toBeNull();
    expect(parseBorders(undefined)).toBeNull();
    expect(parseBorders("x")).toBeNull();
    expect(parseBorders('{"preset":"grid","h":["1","1"],"v":["11"]}')).toBeNull();
    expect(parseBorders('{"preset":"paper","h":["3","1"],"v":["11"]}')).toBeNull();
  });

  it("reads a missing or misfit prop as its preset's lines", () => {
    const t = plain(3, 3);
    expect(bordersOf({}, t)).toEqual(presetLines(3, 3, 1, "paper"));
    const notion = dumpBorders(presetLines(2, 3, 1, "notion"));
    expect(bordersOf({ borders: notion }, t)).toEqual(presetLines(3, 3, 1, "notion"));
  });
});

describe("structure changes (ruling 2)", () => {
  const paper = presetLines(5, 1, 1, "paper"); // h [2,1,0,0,0,2]

  it("inserts a row keeping the end rule at the bottom", () => {
    expect(col(insertRow(paper, 5, 1))).toEqual([2, 1, 0, 0, 0, 0, 2]);
    expect(col(insertRow(paper, 1, 1))).toEqual([2, 1, 0, 0, 0, 0, 2]);
    expect(new Set(insertRow(presetLines(5, 1, 1, "notion"), 3, 1).h)).toEqual(new Set(["1"]));
    const custom: Borders = { ...paper, h: ["2", "1", "0", "2", "0", "2"] };
    const out = insertRow(custom, 3, 1);
    expect(col(out)).toEqual([2, 1, 0, 2, 2, 0, 2]);
    expect(out.v).toHaveLength(6);
  });

  it("gives a new row the vertical lines of the row above it", () => {
    const b: Borders = { preset: "paper", h: ["22", "11", "22"], v: ["020", "101"] };
    expect(insertRow(b, 2, 1).v).toEqual(["020", "101", "101"]);
    expect(insertRow(b, 0, 1).v).toEqual(["020", "020", "101"]);
  });

  it("deletes a row without losing the top or the end rule", () => {
    expect(col(deleteRow(paper, 4))).toEqual([2, 1, 0, 0, 2]);
    expect(col(deleteRow(paper, 0))[0]).toBe(2);
    expect(deleteRow(paper, 0).v).toHaveLength(4);
  });

  it("moves a row's vertical lines with it, not the horizontal ones", () => {
    const b: Borders = { preset: "paper", h: ["22", "11", "00", "22"], v: ["000", "010", "202"] };
    const out = moveRow(b, 2, 0);
    expect(out.h).toEqual(b.h);
    expect(out.v).toEqual(["202", "000", "010"]);
  });

  it("handles columns the same way", () => {
    const b = presetLines(2, 3, 1, "notion");
    expect(insertCol(b, 3).h[0]).toBe("1111");
    expect(insertCol(b, 0).v[0]).toBe("11111");
    const custom: Borders = { preset: "paper", h: ["202", "000", "222"], v: ["0000", "0000"] };
    expect(insertCol(custom, 1).h[0]).toBe("2202");
    expect(deleteCol(custom, 2).h[0]).toBe("20");
    expect(deleteCol(custom, 2).v[0]).toBe("000");
    expect(moveCol(custom, 0, 2).h[0]).toBe("022");
  });

  it("refits: preset lines stay preset, custom lines follow the ops", () => {
    const old = presetLines(3, 2, 1, "paper");
    const shape = { rows: 3, cols: 2, headerRows: 1 };
    const next = { rows: 4, cols: 2, headerRows: 1 };
    expect(refit(old, shape, next, [{ kind: "insertRow", at: 3 }])).toEqual(presetLines(4, 2, 1, "paper"));
    const custom: Borders = { ...old, h: ["22", "11", "22", "22"] };
    expect(refit(custom, shape, next, [{ kind: "insertRow", at: 3 }]).h).toEqual(["22", "11", "22", "22", "22"]);
    // Ops that do not reach the new shape fall back to the preset's lines.
    expect(refit(custom, shape, next, [])).toEqual(presetLines(4, 2, 1, "paper"));
  });
});

describe("border sets on a selection", () => {
  const t = plain(3, 3);

  it("toggles the inner lines of a 2×2 selection", () => {
    const b = presetLines(3, 3, 1, "paper");
    const s = { r0: 1, r1: 2, c0: 0, c1: 1 };
    expect(segments(b, t, s, "inner")).toHaveLength(4);
    const on = applyBorderSet(b, t, s, "inner", 1);
    expect(on.h[2]?.slice(0, 2)).toBe("11");
    expect(on.v[1]?.[1]).toBe("1");
    expect(on.v[2]?.[1]).toBe("1");
    expect(isPressed(on, t, s, "inner", 1)).toBe(true);
    const off = applyBorderSet(on, t, s, "inner", 1);
    expect(off).toEqual(b);
  });

  it("makes the outer lines thick", () => {
    const b = presetLines(3, 3, 1, "paper");
    const s = { r0: 1, r1: 2, c0: 0, c1: 1 };
    const out = applyBorderSet(b, t, s, "outer", 2);
    const segs = segments(out, t, s, "outer");
    expect(segs).toHaveLength(8);
    expect(segs.every((g) => (g.axis === "h" ? out.h : out.v)[g.r]?.[g.c] === "2")).toBe(true);
    expect(applyBorderSet(out, t, s, "clear", 1).h[1]?.slice(0, 2)).toBe("00");
  });

  it("skips the lines inside a merged cell", () => {
    const b = presetLines(5, 3, 2, "paper");
    const s = { r0: 0, r1: 1, c0: 0, c1: 2 };
    const inner = segments(b, MERGED, s, "innerH");
    expect(inner.some((g) => g.r === 1 && g.c === 0)).toBe(false);
    expect(inner.map((g) => g.c)).toEqual([1, 2]);
    const iv = segments(b, MERGED, s, "innerV");
    expect(iv.some((g) => g.r === 0 && g.c === 2)).toBe(false);
    expect(isUsable(b, t, { r0: 1, r1: 1, c0: 0, c1: 2 }, "innerH")).toBe(false);
    expect(isUsable(b, t, { r0: 1, r1: 1, c0: 0, c1: 2 }, "innerV")).toBe(true);
  });

  it("grows a selection to whole merged cells", () => {
    expect(normaliseSelection(MERGED, [1, 0], [1, 0])).toEqual({ r0: 0, r1: 1, c0: 0, c1: 0 });
    expect(normaliseSelection(MERGED, [1, 1], [0, 0])).toEqual({ r0: 0, r1: 1, c0: 0, c1: 2 });
  });

  it("draws a merged cell's side at its heaviest segment", () => {
    const t2: TableContent = {
      type: "tableContent",
      columnWidths: [null, null],
      headerRows: 1,
      rows: [{ cells: [cell("A", { colspan: 2 })] }, { cells: [cell("a"), cell("b")] }],
    };
    const { grid } = tableGrid(t2);
    const b: Borders = { preset: "paper", h: ["02", "00", "22"], v: ["000", "000"] };
    const anchor = grid[0]?.[0];
    if (!anchor || Array.isArray(anchor)) throw new Error("anchor");
    expect(cellEdges(b, anchor)).toEqual({ top: 2, bottom: 0, left: 0, right: 0 });
  });
});

describe("a selection past the table's edge", () => {
  it("applies only to lines the table has (a column dropped while the picker was open)", () => {
    const b = presetLines(2, 2, 1, "paper");
    const t = plain(2, 2);
    const out = applyBorderSet(b, t, { r0: 0, r1: 1, c0: 0, c1: 3 }, "all", 1);
    expect(parseBorders(dumpBorders(out))).not.toBeNull();
    expect(out.h.every((l) => l.length === 2)).toBe(true);
    expect(out.v.every((l) => l.length === 3)).toBe(true);
  });
});
