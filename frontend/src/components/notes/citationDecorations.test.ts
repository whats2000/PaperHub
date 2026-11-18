import { describe, expect, it } from "vitest";

import { citeTitle, findCites } from "@/components/notes/citationDecorations";

describe("findCites", () => {
  it("finds every marker shape edit_note validates, with its range", () => {
    const text =
      "a [chunk:12, 13] b [note:b_1] c [fig:3/fig-1] d [view:3/2/100,100,500,400] e [unsourced:not_read] f [chunk:x]";
    const cites = findCites(text);
    expect(cites.map((c) => [text.slice(c.from, c.to), c.cite])).toEqual([
      ["[chunk:12, 13]", { kind: "chunk", value: "12, 13" }],
      ["[note:b_1]", { kind: "note", value: "b_1" }],
      ["[fig:3/fig-1]", { kind: "fig", value: "3/fig-1" }],
      ["[view:3/2/100,100,500,400]", { kind: "view", value: "3/2/100,100,500,400" }],
      ["[unsourced:not_read]", { kind: "unsourced", value: "not_read" }],
    ]);
  });

  it("titles an unsourced chip with its reason", () => {
    expect(citeTitle({ kind: "unsourced", value: "not_read" })).toContain("not read");
    expect(citeTitle({ kind: "chunk", value: "1" })).toBe("Open the cited passage");
  });
});
