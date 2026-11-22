import { act, fireEvent } from "@testing-library/react";
import { NodeSelection } from "@tiptap/pm/state";
import { afterEach, describe, expect, it } from "vitest";

import { ATOM_CARET_TYPES, ATOM_GUTTER_PX, atomCaret, atomCaretKey } from "./atomCaret";
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
const caretOf = (id: string) =>
  document.querySelector(`.bn-block-outer[data-id="${id}"] [data-atom-caret]`)?.getAttribute("data-atom-caret") ?? null;
const selectedOf = (id: string) => document.querySelector(`.bn-block-outer[data-id="${id}"] [data-object-selected]`);

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
