import { afterEach, describe, expect, it } from "vitest";

import { atomCaret, atomCaretKey, setAtomCaret } from "./atomCaret";
import { paperhubKeys } from "./schema";
import { cleanupEditors, makeEditor, press, type AnyEditor } from "./testEditor";

afterEach(cleanupEditors);

const ids = (editor: AnyEditor) => editor.document.map((b) => b.id);

function lines() {
  return [
    { id: "a", type: "paragraph", content: "one" },
    { id: "b", type: "paragraph", content: "two" },
    { id: "c", type: "paragraph", content: "three" },
  ];
}

describe("Alt+↑ / Alt+↓ moves blocks", () => {
  it("moves the caret's block up and down, the caret staying in it", () => {
    const editor = makeEditor([paperhubKeys, atomCaret], lines());
    editor.setTextCursorPosition("b", "end");
    press(editor, { key: "ArrowUp", altKey: true });
    expect(ids(editor)).toEqual(["b", "a", "c"]);
    expect(editor.getTextCursorPosition().block.id).toBe("b");
    press(editor, { key: "ArrowDown", altKey: true });
    press(editor, { key: "ArrowDown", altKey: true });
    expect(ids(editor)).toEqual(["a", "c", "b"]);
    expect(editor.getTextCursorPosition().block.id).toBe("b");
  });

  it("moves every selected block together", () => {
    const editor = makeEditor([paperhubKeys, atomCaret], lines());
    const view = editor._tiptapEditor.view;
    editor.setTextCursorPosition("a", "start");
    const from = view.state.selection.from;
    editor.setTextCursorPosition("b", "end");
    editor._tiptapEditor.commands.setTextSelection({ from, to: view.state.selection.from });
    press(editor, { key: "ArrowDown", altKey: true });
    expect(ids(editor)).toEqual(["c", "a", "b"]);
  });

  it("moves a reference held by the atom caret, which stays on it", () => {
    const editor = makeEditor(
      [paperhubKeys, atomCaret],
      [
        { id: "a", type: "paragraph", content: "one" },
        { id: "r", type: "divider" },
        { id: "c", type: "paragraph", content: "three" },
      ],
    );
    const view = editor._tiptapEditor.view;
    let pos = -1;
    view.state.doc.descendants((node, p) => {
      if (node.type.name === "divider") pos = p;
    });
    setAtomCaret(view, pos, "after");
    press(editor, { key: "ArrowUp", altKey: true });
    expect(ids(editor)).toEqual(["r", "a", "c"]);
    const caret = atomCaretKey.getState(view.state);
    expect(caret?.side).toBe("after");
    expect(view.state.doc.nodeAt(caret!.pos)?.type.name).toBe("divider");
  });
});
