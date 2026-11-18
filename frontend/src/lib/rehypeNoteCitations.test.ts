import type { Element, Root } from "hast";
import { describe, expect, it } from "vitest";

import { rehypeNoteCitations } from "@/lib/rehypeNoteCitations";

const para = (text: string): Root => ({
  type: "root",
  children: [{ type: "element", tagName: "p", properties: {}, children: [{ type: "text", value: text }] }],
});

function run(text: string): Element["children"] {
  const tree = para(text);
  rehypeNoteCitations()(tree);
  return (tree.children[0] as Element).children;
}

describe("rehypeNoteCitations", () => {
  it("turns each cited block into a note-cite, numbered in first-seen order", () => {
    const out = run("A [note:b_1, note:b_2] then [note:b_1] end");
    expect(out).toEqual([
      { type: "text", value: "A " },
      { type: "element", tagName: "note-cite", properties: { dataBlock: "b_1", dataOrdinal: 1 }, children: [] },
      { type: "element", tagName: "note-cite", properties: { dataBlock: "b_2", dataOrdinal: 2 }, children: [] },
      { type: "text", value: " then " },
      { type: "element", tagName: "note-cite", properties: { dataBlock: "b_1", dataOrdinal: 1 }, children: [] },
      { type: "text", value: " end" },
    ]);
  });

  it("leaves text without a note marker as it is", () => {
    expect(run("no [chunk:3] here")).toEqual([{ type: "text", value: "no [chunk:3] here" }]);
  });
});
