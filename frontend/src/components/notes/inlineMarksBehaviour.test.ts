import { syntaxHighlighter } from "@blocknote/code-block";
import { waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { cleanupEditors, makeEditor, press, runs, type, type AnyEditor } from "@/components/notes/testEditor";

afterEach(cleanupEditors);

const FENCE = "```python ";
const block = (editor: AnyEditor, i = 0) =>
  editor.document[i] as unknown as { type: string; props: { language: string }; content: { text: string }[] };
const tokens = () => document.querySelectorAll('[data-content-type="codeBlock"] [style*="--shiki"]').length;

describe("marks across atoms and existing marks", () => {
  it("a mention chip inside the marks leaves the text intact (no %leaf%, no lost characters)", () => {
    const editor = makeEditor();
    type(editor, "xx **see ");
    editor.insertInlineContent([
      { type: "mention", props: { kind: "paper", paperId: 1, sectionKey: "", label: "Paper" } } as never,
    ]);
    type(editor, "**");
    const json = JSON.stringify(editor.document[0]);
    expect(json).not.toContain("%leaf%");
    expect(json).toContain('"text":"xx **see "');
    expect(json).not.toContain("\"bold\"");
  });

  it("keeps an inner mark when the outer mark closes", () => {
    const editor = makeEditor();
    type(editor, "**a _b_ c**");
    expect(runs(editor)).toEqual([
      { text: "a ", styles: { bold: true } },
      { text: "b", styles: { bold: true, italic: true } },
      { text: " c", styles: { bold: true } },
    ]);
  });
});

describe("keys", () => {
  it("Ctrl+E marks the selection as code and Ctrl+Shift+S as strike", () => {
    const editor = makeEditor();
    type(editor, "abc");
    editor._tiptapEditor.commands.selectAll();
    press(editor, { key: "e", ctrlKey: true });
    expect(runs(editor)).toEqual([{ text: "abc", styles: { code: true } }]);
    press(editor, { key: "e", ctrlKey: true });
    expect(runs(editor)[0]!.styles).toEqual({});
    press(editor, { key: "S", keyCode: 83, ctrlKey: true, shiftKey: true });
    expect(runs(editor)[0]!.styles).toEqual({ strike: true });
  });
});

describe("code block behaviour", () => {
  it("Enter is a new line and Tab indents", () => {
    const editor = makeEditor();
    type(editor, FENCE);
    type(editor, "a");
    press(editor, { key: "Enter" });
    type(editor, "b");
    press(editor, { key: "Tab" });
    type(editor, "c");
    expect(block(editor).content.map((c) => c.text).join("")).toBe("a\nb  c");
    expect(editor.document).toHaveLength(1);
  });

  it("the language label changes the language", () => {
    const editor = makeEditor();
    type(editor, FENCE);
    const select = document.querySelector<HTMLSelectElement>('[data-content-type="codeBlock"] select')!;
    expect(select.value).toBe("python");
    select.value = "rust";
    select.dispatchEvent(new Event("change"));
    expect(block(editor).props.language).toBe("rust");
  });

  it("highlighting follows the language; plain text stays uncoloured", async () => {
    const editor = makeEditor([syntaxHighlighter]);
    type(editor, FENCE);
    type(editor, "def f(): return 1");
    await waitFor(() => expect(tokens()).toBeGreaterThan(0));
    const select = document.querySelector<HTMLSelectElement>('[data-content-type="codeBlock"] select')!;
    select.value = "text";
    select.dispatchEvent(new Event("change"));
    await waitFor(() => expect(tokens()).toBe(0));
  });

  it("an unknown language is uncoloured", async () => {
    const editor = makeEditor([syntaxHighlighter]);
    type(editor, "```nosuchlang ");
    type(editor, "def f(): return 1");
    await new Promise((r) => setTimeout(r, 100));
    expect(tokens()).toBe(0);
  });

  it("Ctrl+Enter puts a new line below when the next block has no text", () => {
    const editor = makeEditor();
    type(editor, FENCE);
    editor.insertBlocks([{ type: "divider" }], editor.document[0]!, "after");
    editor.setTextCursorPosition(editor.document[0]!, "end");
    press(editor, { key: "Enter", ctrlKey: true });
    expect(editor.document.map((b) => b.type)).toEqual(["codeBlock", "paragraph", "divider"]);
    expect(editor.getTextCursorPosition().block.type).toBe("paragraph");
  });

  it("a fence + Enter does not convert a ranged selection, and converts at the end of the line", () => {
    const ranged = makeEditor();
    type(ranged, "```python");
    const end = ranged._tiptapEditor.view.state.selection.from;
    ranged._tiptapEditor.commands.setTextSelection({ from: end - 4, to: end });
    press(ranged, { key: "Enter" });
    expect(ranged.document[0]!.type).not.toBe("codeBlock");
  });

  it("has a Copy button that copies the code and says Copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const editor = makeEditor();
    type(editor, FENCE);
    type(editor, "x = 1");
    await waitFor(() => expect(document.querySelector('[data-content-type="codeBlock"] button.note-code-copy')).not.toBeNull());
    const btn = document.querySelector<HTMLButtonElement>('[data-content-type="codeBlock"] button.note-code-copy')!;
    expect(btn.getAttribute("aria-label")).toBe("Copy");
    btn.click();
    expect(writeText).toHaveBeenCalledWith("x = 1");
    // The button on the page (the editor must not redraw the block over it).
    const shown = () => document.querySelector<HTMLButtonElement>('[data-content-type="codeBlock"] button.note-code-copy')!;
    await waitFor(() => expect(shown().hasAttribute("data-copied")).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(shown()).toBe(btn);
    expect(shown().getAttribute("aria-label")).toBe("Copied");
  });
});

describe("lists", () => {
  it("Enter on an empty nested item steps out a level; again at the top, back to text", async () => {
    const { paperhubKeys } = await import("./schema");
    const editor = makeEditor(
      [paperhubKeys],
      [{ id: "a", type: "bulletListItem", content: "a", children: [{ id: "b", type: "bulletListItem", content: "" }] }],
    );
    const shape = () =>
      (editor.document as unknown as { type: string; children: { type: string }[] }[]).map((b) => [
        b.type,
        b.children.map((c) => c.type),
      ]);
    editor.setTextCursorPosition("b", "start");
    press(editor, { key: "Enter" });
    expect(shape().slice(0, 2)).toEqual([
      ["bulletListItem", []],
      ["bulletListItem", []],
    ]);
    press(editor, { key: "Enter" });
    expect(shape()[1]).toEqual(["paragraph", []]);
    expect(editor.getTextCursorPosition().block.type).toBe("paragraph");
  });
});

describe("Enter inside a fence line", () => {
  it.each([
    ["```py", "thon"],
    ["``", "`python"],
  ])("splits %s|%s into two lines and makes no code block", (before, after) => {
    const editor = makeEditor([], [{ id: "f", type: "paragraph", content: before + after }]);
    editor.setTextCursorPosition("f", "start");
    const at = editor._tiptapEditor.view.state.selection.from + before.length;
    editor._tiptapEditor.commands.setTextSelection(at);
    press(editor, { key: "Enter" });
    const lines = (editor.document as unknown as { type: string; content: { text: string }[] }[]).map((b) => [
      b.type,
      b.content.map((c) => c.text).join(""),
    ]);
    expect(lines.slice(0, 2)).toEqual([
      ["paragraph", before],
      ["paragraph", after],
    ]);
  });
});
