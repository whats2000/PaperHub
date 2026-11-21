import { describe, expect, it } from "vitest";

import { noteOutline, type EditorBlockLike } from "./outline";

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
