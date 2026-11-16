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
});
