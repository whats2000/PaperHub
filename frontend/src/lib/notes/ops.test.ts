import { describe, expect, it } from "vitest";

import { applyOpsLocally } from "@/lib/notes/ops";
import type { NoteBlock } from "@/types/notes";

function nb(id: string, parent: string | null, text = id, extra: Partial<NoteBlock> = {}): NoteBlock {
  return {
    id,
    note_id: 1,
    parent_id: parent,
    type: "paragraph",
    props: {},
    content: [{ type: "text", text, styles: {} }],
    text,
    author: "user",
    section_key: null,
    changed_rev: 1,
    ...extra,
  };
}

describe("applyOpsLocally", () => {
  it("applies insert, update, move and a subtree delete", () => {
    const blocks = [nb("a", null), nb("c", "a"), nb("b", null)];
    const out = applyOpsLocally(1, blocks, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: [{ type: "text", text: "N" }] }, parent_id: null, after: "b" },
      { op: "update", block_id: "b", block: { type: "heading", props: { level: 2 }, content: "B2" } },
      { op: "move", block_id: "n", parent_id: null, before: "b" },
      { op: "delete", block_id: "a" },
    ]);
    expect(out.map((b) => b.id)).toEqual(["n", "b"]);
    expect(out[0]?.text).toBe("N");
    expect(out[1]).toMatchObject({ type: "heading", props: { level: 2 }, text: "B2" });
  });

  it("replaces a block an insert names again instead of adding a second copy", () => {
    const blocks = [nb("a", null), nb("n", null, "old"), nb("b", null)];
    const out = applyOpsLocally(1, blocks, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "new" }, parent_id: null, after: "b" },
    ]);
    expect(out.map((b) => [b.id, b.text])).toEqual([
      ["a", "a"],
      ["b", "b"],
      ["n", "new"],
    ]);
  });
});

describe("applyOpsLocally keeps document order (depth-first, like the server's list)", () => {
  it("a block placed after a parent goes after the parent's children; a moved block takes its children", () => {
    const blocks = [nb("a", null), nb("a1", "a"), nb("a2", "a"), nb("b", null), nb("b1", "b")];
    const out = applyOpsLocally(1, blocks, [
      { op: "move", block_id: "a2", parent_id: null, after: "a" },
      { op: "insert", block_id: "n", block: { type: "divider", props: {} }, parent_id: null, after: "b" },
      { op: "insert", block_id: "c", block: { type: "paragraph", props: {}, content: "C" }, parent_id: "a2" },
      { op: "move", block_id: "b", parent_id: null, before: "a" },
    ]);
    expect(out.map((b) => [b.id, b.parent_id])).toEqual([
      ["b", null],
      ["b1", "b"],
      ["a", null],
      ["a1", "a"],
      ["a2", null],
      ["c", "a2"],
      ["n", null],
    ]);
  });
});
