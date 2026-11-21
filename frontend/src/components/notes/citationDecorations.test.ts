import { describe, expect, it } from "vitest";

import { citationChips, citeTitle, findCites } from "@/components/notes/citationDecorations";
import { cleanupEditors, makeEditor } from "@/components/notes/testEditor";

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

describe("citation chips in table cells", () => {
  it("draws a chip over a marker typed inside a cell", () => {
    const editor = makeEditor([citationChips()], [
      {
        id: "t",
        type: "table",
        content: {
          type: "tableContent",
          columnWidths: [null],
          headerRows: 1,
          rows: [{ cells: ["h"] }, { cells: ["see [chunk:12]"] }],
        },
      },
    ]);
    const chip = editor.domElement!.querySelector<HTMLElement>("td .ph-cite");
    expect(chip?.dataset.cite).toBe("chunk");
    expect(chip?.textContent).toBe("[chunk:12]");
    cleanupEditors();
  });
});
