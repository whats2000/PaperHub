import { act, fireEvent } from "@testing-library/react";
import { NodeSelection } from "@tiptap/pm/state";
import { afterEach, describe, expect, it } from "vitest";

import indexCss from "@/index.css?raw";

import { ATOM_CARET_TYPES, ATOM_GUTTER_PX, atomCaret, atomCaretKey, setAtomCaret } from "./atomCaret";
import { createCaptionHosts, tableCaptions } from "./tableCaptions";
import { cleanupEditors, makeEditor, press, type AnyEditor } from "./testEditor";

afterEach(cleanupEditors);

const table = {
  id: "t1",
  type: "table",
  content: { type: "tableContent", columnWidths: [null, null], rows: [{ cells: ["a", "b"] }, { cells: ["1", "2"] }] },
};
const code = { id: "c1", type: "codeBlock", props: { language: "python" }, content: "x = 1" };
const math = { id: "m1", type: "math", props: { latex: "x^2" } };
const line = { id: "p1", type: "paragraph", content: "one" };

const box = (left: number, right: number, top = 0, bottom = 40) =>
  ({ left, right, top, bottom, width: right - left, height: bottom - top, x: left, y: top, toJSON: () => ({}) }) as DOMRect;

const contentOf = (id: string) =>
  document.querySelector<HTMLElement>(`.bn-block-outer[data-id="${id}"] .bn-block-content`)!;
/** The element carrying `attr` for block `id` (the block itself or one inside it, not a nested block). */
const markOf = (id: string, attr: string) =>
  [...document.querySelectorAll<HTMLElement>(`[${attr}]`)].find((el) => el.closest(".bn-block-outer")?.getAttribute("data-id") === id) ?? null;
const caretOf = (id: string) => markOf(id, "data-atom-caret")?.getAttribute("data-atom-caret") ?? null;
const selectedOf = (id: string) => markOf(id, "data-object-selected");

/** The content node of block `id` (its position in the document). */
function posOf(editor: AnyEditor, id: string): number {
  const view = editor._tiptapEditor.view;
  let pos = -1;
  view.state.doc.descendants((node, p) => {
    if (node.type.name === "blockContainer" && node.attrs.id === id) pos = p + 1;
    return pos < 0;
  });
  return pos;
}

describe("every object block takes the caret", () => {
  it("covers tables, code blocks and math blocks too", () => {
    for (const t of ["table", "codeBlock", "math", "paperQuote", "divider", "section", "toc"])
      expect(ATOM_CARET_TYPES.has(t)).toBe(true);
  });

  // (A table's node selection becomes the table plugin's all-cells
  // selection: it is marked once the caret is beside it, below.)
  it.each([
    ["code block", code],
    ["math block", math],
  ])("a selected %s is marked as a selected object (the tint), not framed", (_name, block) => {
    const editor = makeEditor([atomCaret], [line, block]);
    const view = editor._tiptapEditor.view;
    act(() => view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, posOf(editor, block.id)))));
    expect(selectedOf(block.id)).not.toBeNull();
    expect(selectedOf("p1")).toBeNull();
    // No caret without a click or a key beside it.
    expect(caretOf(block.id)).toBeNull();
  });

  it("a click in the gutter right of a table puts the caret on its right; Enter opens a line below", () => {
    const editor = makeEditor([atomCaret], [line, table]);
    const view = editor._tiptapEditor.view;
    contentOf("t1").getBoundingClientRect = () => box(100, 500, 50, 150);
    act(() => {
      fireEvent.click(view.dom, { clientX: 500 + ATOM_GUTTER_PX - 2, clientY: 90 });
    });
    expect(caretOf("t1")).toBe("after");
    expect(selectedOf("t1")).not.toBeNull();
    press(editor, { key: "Enter" });
    expect(editor.document.map((b) => b.type)).toEqual(["paragraph", "table", "paragraph"]);
  });

  it("Backspace at a table's right caret removes the table, not just its cells' text", () => {
    const editor = makeEditor([atomCaret], [line, table]);
    contentOf("t1").getBoundingClientRect = () => box(100, 500, 50, 150);
    act(() => {
      fireEvent.click(editor._tiptapEditor.view.dom, { clientX: 503, clientY: 90 });
    });
    press(editor, { key: "Backspace" });
    expect(editor.document.map((b) => b.type)).toEqual(["paragraph"]);
  });

  it("a click in the gutter left of a block puts the caret on its left", () => {
    makeEditor([atomCaret], [line, code]);
    contentOf("c1").getBoundingClientRect = () => box(100, 500, 50, 150);
    const view = document.querySelector<HTMLElement>(".ProseMirror")!;
    act(() => {
      fireEvent.click(view, { clientX: 100 - ATOM_GUTTER_PX + 2, clientY: 60 });
    });
    expect(caretOf("c1")).toBe("before");
  });

  it("a click past the gutter, or beside no block, sets nothing", () => {
    makeEditor([atomCaret], [line, table]);
    contentOf("t1").getBoundingClientRect = () => box(100, 500, 50, 150);
    const view = document.querySelector<HTMLElement>(".ProseMirror")!;
    act(() => {
      fireEvent.click(view, { clientX: 500 + ATOM_GUTTER_PX + 6, clientY: 90 });
    });
    expect(caretOf("t1")).toBeNull();
    act(() => {
      fireEvent.click(view, { clientX: 505, clientY: 200 });
    });
    expect(caretOf("t1")).toBeNull();
  });

  it("a click in a table cell edits the cell; beside the table (in its block), the caret", () => {
    const editor = makeEditor([atomCaret], [line, table]);
    const cell = contentOf("t1").querySelector("td, th") as HTMLElement;
    act(() => {
      fireEvent.click(cell, { clientX: 120, clientY: 60 });
    });
    expect(caretOf("t1")).toBeNull();
    expect(editor._tiptapEditor.view.state.selection instanceof NodeSelection).toBe(false);
    const t = contentOf("t1").querySelector("table")!;
    t.getBoundingClientRect = () => box(100, 260, 50, 150);
    contentOf("t1").getBoundingClientRect = () => box(100, 500, 50, 150);
    act(() => {
      fireEvent.click(t.parentElement!, { clientX: 300, clientY: 60 });
    });
    expect(caretOf("t1")).toBe("after");
  });

  it("a click in a code block edits its code", () => {
    makeEditor([atomCaret], [line, code]);
    const pre = contentOf("c1").querySelector("pre")!;
    act(() => {
      fireEvent.click(pre, { clientX: 120, clientY: 60 });
    });
    expect(caretOf("c1")).toBeNull();
  });

  it("the caret's state follows the click (one caret at a time)", () => {
    const editor = makeEditor([atomCaret], [line, table, code]);
    const view = editor._tiptapEditor.view;
    contentOf("t1").getBoundingClientRect = () => box(100, 500, 50, 150);
    contentOf("c1").getBoundingClientRect = () => box(100, 500, 160, 220);
    act(() => {
      fireEvent.click(view.dom, { clientX: 503, clientY: 200 });
    });
    expect(caretOf("c1")).toBe("after");
    expect(caretOf("t1")).toBeNull();
    expect(atomCaretKey.getState(view.state)?.side).toBe("after");
  });
});

/** The declarations of the index.css rule whose selector list holds `selector`. */
function cssRule(selector: string): string {
  for (const m of indexCss.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const sels = m[1]!.split(",").map((s) => s.trim().replace(/\s+/g, " "));
    if (sels.includes(selector)) return m[2]!;
  }
  return "";
}

describe("the selected object block's tint", () => {
  const captioned = { ...table, props: { caption: "Results", notes: "* p < 0.05" } };

  // The mark is on the whole block (.bn-block-outer), not on the content
  // node: a table's caption (above) and notes (below) are drawn outside
  // its .bn-block-content, so a tint on the content missed them.
  it.each([
    ["table", table],
    ["code block", code],
  ])("a selected %s is marked on its block", (_name, block) => {
    const editor = makeEditor([atomCaret], [line, block]);
    contentOf(block.id).getBoundingClientRect = () => box(100, 500, 50, 150);
    act(() => {
      fireEvent.click(editor._tiptapEditor.view.dom, { clientX: 503, clientY: 90 });
    });
    const marked = selectedOf(block.id);
    expect(marked?.classList.contains("bn-block-outer")).toBe(true);
    expect(marked?.getAttribute("data-id")).toBe(block.id);
    expect(marked?.getAttribute("data-atom-caret")).toBe("after");
  });

  it("a captioned table's mark holds its caption above and its notes below", () => {
    const editor = makeEditor([atomCaret, tableCaptions(createCaptionHosts())], [line, captioned]);
    contentOf("t1").getBoundingClientRect = () => box(100, 500, 50, 150);
    act(() => {
      fireEvent.click(editor._tiptapEditor.view.dom, { clientX: 503, clientY: 90 });
    });
    const marked = selectedOf("t1")!;
    const block = marked.querySelector(":scope > .bn-block")!;
    const parts = [...block.children].map((el) =>
      el.classList.contains("note-table-part-host") ? "host" : el.classList.contains("bn-block-content") ? "table" : "other",
    );
    // The tint's three rows: caption host, table, notes host.
    expect(parts.slice(0, 3)).toEqual(["host", "table", "host"]);
    expect(marked.getAttribute("data-atom-caret")).toBe("after");
  });

  it("a nested block under a selected table is not marked (no doubled tint)", () => {
    const editor = makeEditor([atomCaret], [line, { ...table, children: [{ id: "k1", type: "paragraph", content: "child" }] }]);
    const view = editor._tiptapEditor.view;
    act(() => setAtomCaret(view, posOf(editor, "t1"), "after"));
    expect(selectedOf("t1")).not.toBeNull();
    expect(selectedOf("k1")).toBeNull();
    expect(document.querySelectorAll("[data-object-selected]")).toHaveLength(1);
  });

  it("a click in the gutter beside a table's caption puts the caret on the table", () => {
    const editor = makeEditor([atomCaret, tableCaptions(createCaptionHosts())], [line, captioned]);
    contentOf("t1").getBoundingClientRect = () => box(100, 500, 50, 150);
    const [caption, notes] = document.querySelectorAll<HTMLElement>('.bn-block-outer[data-id="t1"] .note-table-part-host');
    caption!.getBoundingClientRect = () => box(100, 500, 24, 50);
    notes!.getBoundingClientRect = () => box(100, 500, 150, 170);
    act(() => {
      fireEvent.click(editor._tiptapEditor.view.dom, { clientX: 97, clientY: 30 });
    });
    expect(caretOf("t1")).toBe("before");
    act(() => {
      fireEvent.click(editor._tiptapEditor.view.dom, { clientX: 504, clientY: 165 });
    });
    expect(caretOf("t1")).toBe("after");
  });

  it("the tint and the caret are drawn on the block's .bn-block over its own row", () => {
    const grid = cssRule(".paperhub-notes .bn-block-outer[data-object-selected] > .bn-block");
    expect(grid).toMatch(/display:\s*grid/);
    expect(grid).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\)/);
    const tint = cssRule(".paperhub-notes .bn-block-outer[data-object-selected] > .bn-block::before");
    expect(tint).toMatch(/inset:\s*0 -8px/);
    expect(tint).toMatch(/grid-area:\s*1 \/ 1/);
    const caret = cssRule(".paperhub-notes .bn-block-outer[data-atom-caret] > .bn-block::after");
    expect(caret).toMatch(/grid-area:\s*1 \/ 1/);
  });

  it("on a table they span its caption, table and notes (rows 1 to 3), not what follows", () => {
    for (const pseudo of ["::before", "::after"]) {
      const rule = cssRule(`.paperhub-notes .bn-block-outer > .bn-block:has(> .note-table-part-host)${pseudo}`);
      expect(rule).toMatch(/grid-row:\s*1 \/ 4/);
    }
  });
});
