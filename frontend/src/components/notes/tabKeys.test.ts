import { afterEach, describe, expect, it } from "vitest";

import { tabStays } from "./tabKeys";
import { cleanupEditors, makeEditor, type AnyEditor } from "./testEditor";

afterEach(cleanupEditors);

const lines = [
  { id: "a", type: "paragraph", content: "123" },
  { id: "b", type: "paragraph", content: "124" },
  { id: "c", type: "paragraph", content: "125" },
];

/** A Tab pressed in the editor, as the browser delivers it. */
function tab(editor: AnyEditor, shift = false) {
  const e = new KeyboardEvent("keydown", { key: "Tab", code: "Tab", shiftKey: shift, bubbles: true, cancelable: true });
  editor._tiptapEditor.view.dom.dispatchEvent(e);
  return e;
}
const depth = (editor: AnyEditor, id: string): number => {
  const parent = editor.getParentBlock(id);
  return parent ? 1 + depth(editor, parent.id) : 0;
};

describe("tabStays", () => {
  it("Tab nests while the block above allows it, then does nothing: the caret stays in the note", () => {
    const editor = makeEditor([tabStays], lines);
    editor.setTextCursorPosition("c", "end");
    expect(tab(editor).defaultPrevented).toBe(true);
    expect(depth(editor, "c")).toBe(1);
    // Under 124 there is nothing to nest under: a no-op, not the browser's
    // Tab (which moves focus out of the note and scrolls it to the top).
    const at = editor._tiptapEditor.view.state.selection.from;
    const before = JSON.stringify(editor.document);
    expect(tab(editor).defaultPrevented).toBe(true);
    expect(JSON.stringify(editor.document)).toBe(before);
    expect(editor._tiptapEditor.view.state.selection.from).toBe(at);
  });

  it("successive nesting still works when the block above is nested", () => {
    const editor = makeEditor([tabStays], lines);
    editor.setTextCursorPosition("b", "end");
    tab(editor);
    editor.setTextCursorPosition("c", "end");
    tab(editor);
    tab(editor);
    expect(depth(editor, "c")).toBe(2);
  });

  it("Shift+Tab steps out, and at the top level does nothing", () => {
    const editor = makeEditor([tabStays], lines);
    editor.setTextCursorPosition("b", "end");
    tab(editor);
    expect(tab(editor, true).defaultPrevented).toBe(true);
    expect(depth(editor, "b")).toBe(0);
    expect(tab(editor, true).defaultPrevented).toBe(true);
    expect(depth(editor, "b")).toBe(0);
  });

  it("Ctrl+Tab and the first line's Tab with nothing above are not taken from the browser beyond the note", () => {
    const editor = makeEditor([tabStays], lines);
    editor.setTextCursorPosition("a", "end");
    expect(tab(editor).defaultPrevented).toBe(true);
    const ctrl = new KeyboardEvent("keydown", { key: "Tab", ctrlKey: true, bubbles: true, cancelable: true });
    editor._tiptapEditor.view.dom.dispatchEvent(ctrl);
    expect(ctrl.defaultPrevented).toBe(false);
  });
});
