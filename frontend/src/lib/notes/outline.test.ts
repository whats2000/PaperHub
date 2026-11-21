import { describe, expect, it } from "vitest";

import { noteOutline, storedNoteOutline, type EditorBlockLike } from "./outline";

const run = (text: string) => [{ type: "text", text, styles: {} }];
const h = (id: string, level: number, text: string, children: EditorBlockLike[] = []): EditorBlockLike => ({
  id,
  type: "heading",
  props: { level },
  content: run(text),
  children,
});
const p = (id: string, text: string): EditorBlockLike => ({ id, type: "paragraph", props: {}, content: run(text), children: [] });

describe("noteOutline", () => {
  it("lists headings 1-3 and sections (level 1) in document order, children included", () => {
    const blocks: EditorBlockLike[] = [
      { id: "s1", type: "section", props: { title: "2 Method", sectionKey: "2 Method" }, children: [] },
      h("a", 2, "Setup", [p("x", "body"), h("b", 3, "Nested")]),
      h("c", 1, "Result"),
    ];
    expect(noteOutline(blocks)).toEqual([
      { blockId: "s1", text: "2 Method", level: 1 },
      { blockId: "a", text: "Setup", level: 2 },
      { blockId: "b", text: "Nested", level: 3 },
      { blockId: "c", text: "Result", level: 1 },
    ]);
  });

  it("names a section by its key when its title is blank", () => {
    const blocks: EditorBlockLike[] = [
      { id: "s", type: "section", props: { title: " ", sectionKey: "3 Results" }, children: [] },
      { id: "t", type: "section", props: { title: "", sectionKey: "" }, children: [] },
    ];
    expect(noteOutline(blocks)).toEqual([{ blockId: "s", text: "3 Results", level: 1 }]);
  });

  it("skips headings above level 3, empty headings and other blocks", () => {
    const blocks: EditorBlockLike[] = [
      h("a", 4, "Too deep"),
      h("b", 2, "   "),
      { id: "c", type: "heading", props: { level: 2 }, content: [], children: [] },
      p("d", "text"),
      h("e", 1, "Kept"),
    ];
    expect(noteOutline(blocks).map((e) => e.blockId)).toEqual(["e"]);
  });

  it("reads link and inline-math text of a heading", () => {
    const blocks: EditorBlockLike[] = [
      {
        id: "a",
        type: "heading",
        props: { level: 1 },
        content: [
          { type: "text", text: "On ", styles: {} },
          { type: "link", href: "x", content: [{ type: "text", text: "links", styles: {} }] },
        ],
        children: [],
      },
    ];
    expect(noteOutline(blocks)[0]!.text).toBe("On links");
  });
});

describe("storedNoteOutline", () => {
  it("lists stored blocks in document order, a nested heading under its parent", () => {
    const row = (id: string, text: string, level: number, parent_id: string | null = null) => ({
      id,
      parent_id,
      type: "heading",
      props: { level },
      content: [{ type: "text", text, styles: {} }],
    });
    // "b" is a child of "a" but stored after "c".
    const blocks = [row("a", "Alpha", 1), row("c", "Gamma", 1), row("b", "Beta", 2, "a")];
    expect(storedNoteOutline(blocks).map((e) => e.text)).toEqual(["Alpha", "Beta", "Gamma"]);
  });

  it("keeps siblings in the store array's order (the document order; there is no order_key here)", () => {
    const h = (id: string, text: string, parent_id: string | null) => ({
      id,
      parent_id,
      type: "heading",
      props: { level: 2 },
      content: text,
    });
    const blocks = [h("z", "First", null), h("m", "Child one", "z"), h("a", "Child two", "z"), h("b", "Last", null)];
    expect(storedNoteOutline(blocks).map((e) => e.text)).toEqual(["First", "Child one", "Child two", "Last"]);
  });

  it("keeps a block whose parent is missing, after the tree", () => {
    const blocks = [
      { id: "x", parent_id: "gone", type: "heading", props: { level: 1 }, content: "Orphan" },
      { id: "a", parent_id: null, type: "heading", props: { level: 1 }, content: "Alpha" },
    ];
    expect(storedNoteOutline(blocks).map((e) => e.text)).toEqual(["Alpha", "Orphan"]);
  });
});
