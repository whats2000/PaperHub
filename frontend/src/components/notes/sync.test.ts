import { describe, expect, it } from "vitest";

import {
  diffDocuments,
  pruneUnsavedEmpty,
  toEditorBlocks,
  type EditorBlock,
} from "@/components/notes/sync";
import type { NoteBlock } from "@/types/notes";

function p(id: string, text: string, children: EditorBlock[] = []): EditorBlock {
  return {
    id,
    type: "paragraph",
    props: {},
    content: text ? [{ type: "text", text, styles: {} }] : [],
    children,
  };
}

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

describe("diffDocuments", () => {
  it("returns no ops for identical documents", () => {
    const doc = [p("a", "A"), p("b", "B")];
    expect(diffDocuments(doc, structuredClone(doc))).toEqual([]);
  });

  it("inserts a new block after its previous sibling", () => {
    const ops = diffDocuments([p("a", "A"), p("b", "B")], [p("a", "A"), p("n", "N"), p("b", "B")]);
    expect(ops).toEqual([
      {
        op: "insert",
        block_id: "n",
        block: { type: "paragraph", props: {}, content: [{ type: "text", text: "N", styles: {} }] },
        parent_id: null,
        after: "a",
      },
    ]);
  });

  it("inserts a new first block before the first unchanged sibling", () => {
    const ops = diffDocuments([p("a", "A")], [p("n", "N"), p("a", "A")]);
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ op: "insert", block_id: "n", parent_id: null, before: "a" });
    expect(ops[0]?.after).toBeUndefined();
  });

  it("updates a block whose content changed", () => {
    const ops = diffDocuments([p("a", "A")], [p("a", "A!")]);
    expect(ops).toEqual([
      {
        op: "update",
        block_id: "a",
        block: { type: "paragraph", props: {}, content: [{ type: "text", text: "A!", styles: {} }] },
      },
    ]);
  });

  it("deletes a removed block once, not its removed children", () => {
    const ops = diffDocuments([p("a", "A", [p("c", "C")]), p("b", "B")], [p("b", "B")]);
    expect(ops).toEqual([{ op: "delete", block_id: "a" }]);
  });

  it("moves only the block that changed place", () => {
    const ops = diffDocuments(
      [p("a", "A"), p("b", "B"), p("c", "C")],
      [p("b", "B"), p("c", "C"), p("a", "A")],
    );
    expect(ops).toEqual([{ op: "move", block_id: "a", parent_id: null, after: "c" }]);
  });

  it("nests a block under its new parent (Tab)", () => {
    const ops = diffDocuments([p("a", "A"), p("b", "B")], [p("a", "A", [p("b", "B")])]);
    expect(ops).toEqual([{ op: "move", block_id: "b", parent_id: "a" }]);
  });

  it("inserts a new nested child under its parent", () => {
    const ops = diffDocuments(
      [p("a", "A", [p("x", "X")])],
      [p("a", "A", [p("x", "X"), p("y", "Y")])],
    );
    expect(ops).toEqual([
      expect.objectContaining({ op: "insert", block_id: "y", parent_id: "a", after: "x" }),
    ]);
  });

  it("deletes the last block first so a section empties before its heading goes", () => {
    const ops = diffDocuments([p("s", "S"), p("x", "X"), p("keep", "K")], [p("keep", "K")]);
    expect(ops).toEqual([
      { op: "delete", block_id: "x" },
      { op: "delete", block_id: "s" },
    ]);
  });

  it("moves a child out before its old parent is deleted", () => {
    const ops = diffDocuments([p("a", "A", [p("c", "C")]), p("b", "B")], [p("b", "B"), p("c", "C")]);
    expect(ops.map((o) => o.op)).toEqual(["move", "delete"]);
    expect(ops[0]).toEqual({ op: "move", block_id: "c", parent_id: null, after: "b" });
    expect(ops[1]).toEqual({ op: "delete", block_id: "a" });
  });

  it("sends a section block's key as section_key and JSON props decoded", () => {
    const prev: EditorBlock[] = [];
    const next: EditorBlock[] = [
      {
        id: "q",
        type: "paperQuote",
        props: { markId: "mk_1", color: "green", quote: "x", pieces: '[{"quote":"x"}]', source: "{}", lang: "", orig: "", anchorLost: false, piecesLost: 0, kind: "highlight" },
        content: undefined,
        children: [],
      },
    ];
    const ops = diffDocuments(prev, next);
    expect(ops[0]?.block).toEqual({
      type: "paperQuote",
      props: { markId: "mk_1", color: "green", quote: "x", pieces: [{ quote: "x" }], source: {}, kind: "highlight" },
    });
  });
});

describe("toEditorBlocks", () => {
  it("nests flat blocks by parent and encodes complex props", () => {
    const tree = toEditorBlocks([
      nb("s", null, "Intro", { type: "section", props: { title: "1 Intro" }, content: null, section_key: "1 Intro" }),
      nb("a", null),
      nb("c", "a"),
      nb("q", null, "x", { type: "paperQuote", props: { markId: "m", pieces: [{ quote: "x" }], source: { page: 2 } }, content: null }),
    ]);
    expect(tree.map((b) => b.id)).toEqual(["s", "a", "q"]);
    expect(tree[0]?.props).toEqual({ title: "1 Intro", sectionKey: "1 Intro" });
    expect(tree[1]?.children.map((b) => b.id)).toEqual(["c"]);
    expect(tree[2]?.props.pieces).toBe('[{"quote":"x"}]');
    expect(tree[2]?.props.source).toBe('{"page":2}');
    expect(tree[2]?.content).toBeUndefined();
  });
});

describe("pruneUnsavedEmpty", () => {
  it("drops an empty paragraph the server has never seen", () => {
    const known = new Set(["a", "b"]);
    const out = pruneUnsavedEmpty([p("a", "A"), p("new", ""), p("b", "")], known);
    expect(out.map((b) => b.id)).toEqual(["a", "b"]);
  });
});

describe("a divider lifted to the top level", () => {
  it("is one update and one move, and the store's copy round-trips to the editor's tree", async () => {
    const { applyOpsLocally } = await import("@/lib/notes/ops");
    const { cleanupEditors, makeEditor, type } = await import("./testEditor");
    const { dividerRule } = await import("./inlineMarks");
    const stored = [nb("l1", null, "a"), nb("l2", "l1", "b"), nb("l3", "l1", ""), nb("after", null, "after")];
    const editor = makeEditor([dividerRule], toEditorBlocks(stored));
    const before = JSON.parse(JSON.stringify(editor.document)) as EditorBlock[];
    editor.setTextCursorPosition("l3", "start");
    type(editor, "---");
    const after = JSON.parse(JSON.stringify(editor.document)) as EditorBlock[];
    const ops = diffDocuments(before, after);
    expect(ops.map((o) => [o.op, o.block_id])).toEqual([
      ["move", "l3"],
      ["update", "l3"],
    ]);
    const move = ops.find((o) => o.op === "move")!;
    expect([move.parent_id ?? null, move.after]).toEqual([null, "l1"]);
    // The store applies them as the server would: the same tree comes back.
    const local = applyOpsLocally(1, stored, ops);
    expect(local.map((b) => [b.id, b.parent_id, b.type])).toEqual([
      ["l1", null, "paragraph"],
      ["l2", "l1", "paragraph"],
      ["l3", null, "divider"],
      ["after", null, "paragraph"],
    ]);
    const shape = (t: EditorBlock[]): unknown => t.map((b) => [b.id, b.type, shape(b.children)]);
    expect(shape(toEditorBlocks(local))).toEqual(shape(after));
    cleanupEditors();
  });
});
