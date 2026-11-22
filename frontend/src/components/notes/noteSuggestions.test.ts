import { describe, expect, it } from "vitest";

import { enterTracker, tableCellChanges } from "@/components/notes/noteSuggestions";
import type { NoteSuggestion } from "@/types/notes";

const cell = (text: string) => ({ type: "tableCell", props: {}, content: text ? [{ type: "text", text, styles: {} }] : [] });
const table = (rows: string[][]) => ({
  type: "tableContent",
  headerRows: 1,
  rows: rows.map((r) => ({ cells: r.map(cell) })),
});
const sug = (rows: string[][]): NoteSuggestion => ({
  id: "sg_1",
  block_id: "t1",
  change_set_id: "cs_1",
  kind: "update",
  proposed: { type: "table", props: {}, content: table(rows) },
  status: "open",
  created_at: "",
});
const CURRENT = table([
  ["Method", "Context", "Latency"],
  ["RoboTTT", "8K steps", "constant"],
  ["pi0", "", "last"],
]);

describe("tableCellChanges", () => {
  it("names a changed cell by its row's first cell and its column's header", () => {
    const out = tableCellChanges(sug([
      ["Method", "Context", "Latency"],
      ["RoboTTT", "8K steps", "constant"],
      ["pi0", "", "short-history"],
    ]), CURRENT);
    expect(out).toEqual([
      { kind: "cell", row: "pi0", rowN: 3, column: "Latency", colN: 3, from: "last", to: "short-history" },
    ]);
  });

  it("lists an added and a removed row as whole rows, the others unchanged", () => {
    const added = tableCellChanges(sug([
      ["Method", "Context", "Latency"],
      ["RoboTTT", "8K steps", "constant"],
      ["Octo", "2 steps", "constant"],
      ["pi0", "", "last"],
    ]), CURRENT);
    expect(added).toEqual([
      { kind: "addRow", row: "Octo", rowN: 3, column: "", colN: 0, from: "", to: "Octo | 2 steps | constant" },
    ]);
    const removed = tableCellChanges(sug([
      ["Method", "Context", "Latency"],
      ["pi0", "", "last"],
    ]), CURRENT);
    expect(removed).toEqual([
      { kind: "removeRow", row: "RoboTTT", rowN: 2, column: "", colN: 0, from: "RoboTTT | 8K steps | constant", to: "" },
    ]);
  });

  it("is empty for anything but a table update", () => {
    expect(tableCellChanges({ ...sug([]), kind: "delete" }, CURRENT)).toEqual([]);
    expect(tableCellChanges({ ...sug([]), proposed: { type: "paragraph", content: [] } }, CURRENT)).toEqual([]);
  });
});

describe("enterTracker", () => {
  it("fades a suggestion in once, and forgets the ones no longer open", () => {
    let t = 1000;
    const seen = enterTracker(() => t);
    expect(seen.entering("s1")).toBe(true);
    expect(seen.entering("s2")).toBe(true);
    t += 10_000;
    expect(seen.entering("s1")).toBe(false);
    seen.keep(["s2"]);
    expect(seen.size()).toBe(1);
    seen.keep([]);
    expect(seen.size()).toBe(0);
  });
});
