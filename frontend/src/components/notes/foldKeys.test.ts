import { afterEach, describe, expect, it, vi } from "vitest";

import { foldKeyGuard, foldKeyOf } from "./foldKeys";
import { cleanupEditors, makeEditor, type } from "./testEditor";

afterEach(() => {
  cleanupEditors();
  vi.restoreAllMocks();
});

const line = { id: "h1", type: "heading", props: { level: 2 }, content: "Method" };
const textOf = (editor: ReturnType<typeof makeEditor>) => editor._tiptapEditor.view.state.doc.textContent;

/** A key event fired at the editor the way the browser fires it. */
function key(target: HTMLElement, kind: "keydown" | "keyup", init: KeyboardEventInit) {
  const e = new KeyboardEvent(kind, { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(e);
  return e;
}

describe("foldKeyOf", () => {
  const win = () => vi.spyOn(navigator, "platform", "get").mockReturnValue("Win32");
  const mac = () => vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");

  it("matches the bracket keys by their place on the keyboard, not the character they type", () => {
    win();
    // Shift+[ types `{` on a US layout; an IME reports "Process".
    expect(foldKeyOf({ code: "BracketLeft", key: "{", ctrlKey: true, shiftKey: true, altKey: false, metaKey: false })).toBe("fold");
    expect(foldKeyOf({ code: "BracketLeft", key: "Process", ctrlKey: true, shiftKey: true, altKey: false, metaKey: false })).toBe("fold");
    expect(foldKeyOf({ code: "BracketRight", key: "}", ctrlKey: true, shiftKey: true, altKey: false, metaKey: false })).toBe("unfold");
    // Not the chord: no Shift, Alt added, or another key.
    expect(foldKeyOf({ code: "BracketLeft", key: "[", ctrlKey: true, shiftKey: false, altKey: false, metaKey: false })).toBeNull();
    expect(foldKeyOf({ code: "BracketLeft", key: "{", ctrlKey: true, shiftKey: true, altKey: true, metaKey: false })).toBeNull();
    expect(foldKeyOf({ code: "KeyS", key: "S", ctrlKey: true, shiftKey: true, altKey: false, metaKey: false })).toBeNull();
  });

  it("on a Mac it is ⌘⌥[ / ] (⌘⇧[ / ] switches the browser's tabs)", () => {
    mac();
    expect(foldKeyOf({ code: "BracketLeft", key: "“", ctrlKey: false, shiftKey: false, altKey: true, metaKey: true })).toBe("fold");
    expect(foldKeyOf({ code: "BracketRight", key: "‘", ctrlKey: false, shiftKey: false, altKey: true, metaKey: true })).toBe("unfold");
    expect(foldKeyOf({ code: "BracketLeft", key: "{", ctrlKey: false, shiftKey: true, altKey: false, metaKey: true })).toBeNull();
    expect(foldKeyOf({ code: "BracketLeft", key: "{", ctrlKey: true, shiftKey: true, altKey: false, metaKey: false })).toBeNull();
  });
});

describe("foldKeyGuard", () => {
  it("an IME's `{` that comes with the fold keys is never typed into the note", () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Win32");
    const editor = makeEditor([foldKeyGuard], [line]);
    const dom = editor._tiptapEditor.view.dom;
    editor.setTextCursorPosition(editor.document[0]!, "end");
    key(dom, "keydown", { code: "BracketLeft", key: "Process", keyCode: 229, ctrlKey: true, shiftKey: true });
    // The IME commits its text: as a cancelable beforeinput ...
    const before = new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: "{" });
    dom.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(true);
    // ... or straight into the page, read back by ProseMirror.
    type(editor, "{");
    type(editor, "「");
    expect(textOf(editor)).toBe("Method");
    key(dom, "keyup", { code: "BracketLeft", key: "{", ctrlKey: true, shiftKey: true });
  });

  it("once the keys are let go, brackets type as usual", async () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Win32");
    const editor = makeEditor([foldKeyGuard], [line]);
    const dom = editor._tiptapEditor.view.dom;
    editor.setTextCursorPosition(editor.document[0]!, "end");
    key(dom, "keydown", { code: "BracketRight", key: "}", ctrlKey: true, shiftKey: true });
    key(dom, "keyup", { code: "BracketRight", key: "}", ctrlKey: true, shiftKey: true });
    await new Promise((r) => setTimeout(r, 0));
    type(editor, "{x}");
    const before = new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: "{" });
    dom.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(false);
    expect(textOf(editor)).toBe("Method{x}");
  });

  it("only the fold keys hold text back: Shift+[ alone types `{`", () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Win32");
    const editor = makeEditor([foldKeyGuard], [line]);
    const dom = editor._tiptapEditor.view.dom;
    editor.setTextCursorPosition(editor.document[0]!, "end");
    key(dom, "keydown", { code: "BracketLeft", key: "{", shiftKey: true });
    type(editor, "{");
    expect(textOf(editor)).toBe("Method{");
  });

  it("a fold chord that never gets its key up stops holding text back on the next key", () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Win32");
    const editor = makeEditor([foldKeyGuard], [line]);
    const dom = editor._tiptapEditor.view.dom;
    editor.setTextCursorPosition(editor.document[0]!, "end");
    key(dom, "keydown", { code: "BracketLeft", key: "{", ctrlKey: true, shiftKey: true });
    key(dom, "keydown", { code: "KeyA", key: "a" });
    type(editor, "a");
    expect(textOf(editor)).toBe("Methoda");
  });
});
