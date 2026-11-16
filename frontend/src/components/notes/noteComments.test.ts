import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyCommentStyle, commentKeys, START_COMMENT_EVENT, stripCommentStyle } from "@/components/notes/noteComments";
import { cleanupEditors, makeEditor, press } from "@/components/notes/testEditor";

afterEach(cleanupEditors);

const para = (content: unknown) => [{ id: "p1", type: "paragraph", content }];
const commented = [
  { type: "text", text: "see ", styles: {} },
  { type: "text", text: "this", styles: { comment: "c_1", bold: true } },
  { type: "text", text: " now", styles: {} },
];

describe("Ctrl+Shift+M", () => {
  const start = vi.fn();
  beforeEach(() => document.addEventListener(START_COMMENT_EVENT, start));
  afterEach(() => {
    document.removeEventListener(START_COMMENT_EVENT, start);
    start.mockReset();
  });

  it("starts a comment", () => {
    const editor = makeEditor([commentKeys]);
    press(editor, { key: "M", keyCode: 77, ctrlKey: true, shiftKey: true });
    expect(start).toHaveBeenCalledTimes(1);
  });

  it("is not Ctrl+M or Ctrl+Shift+S", () => {
    const editor = makeEditor([commentKeys]);
    press(editor, { key: "m", keyCode: 77, ctrlKey: true });
    press(editor, { key: "S", keyCode: 83, ctrlKey: true, shiftKey: true });
    expect(start).not.toHaveBeenCalled();
  });
});

describe("the comment style", () => {
  it("carries the thread id on the words and survives a round trip", () => {
    const editor = makeEditor([], para(commented));
    const runs = (editor.document[0]!.content as { text: string; styles: Record<string, unknown> }[]).map(
      (r) => [r.text, r.styles.comment],
    );
    expect(runs).toEqual([["see ", undefined], ["this", "c_1"], [" now", undefined]]);
  });

  it("is taken off by id, other styles kept", () => {
    const editor = makeEditor([], para(commented));
    stripCommentStyle(editor, "c_1");
    const after = editor.document[0]!.content as { text: string; styles: Record<string, unknown> }[];
    expect(after.some((r) => r.styles.comment)).toBe(false);
    expect(after.find((r) => r.text === "this")!.styles.bold).toBe(true);
  });

  it("is put back over the quote in the CURRENT text, so edits made meanwhile stay", () => {
    const editor = makeEditor([], para(commented));
    stripCommentStyle(editor, "c_1");
    // an edit while the Undo toast was up: words before the quote changed
    editor.updateBlock("p1", { content: "now see this today" });
    expect(applyCommentStyle(editor, "c_1", "p1", "this")).toBe(true);
    const runs = editor.document[0]!.content as { text: string; styles: Record<string, unknown> }[];
    expect(runs.map((r) => r.text).join("")).toBe("now see this today");
    expect(runs.filter((r) => r.styles.comment === "c_1").map((r) => r.text)).toEqual(["this"]);
  });

  it("finds a quote that spans several runs", () => {
    const editor = makeEditor([], para([
      { type: "text", text: "a ", styles: {} },
      { type: "text", text: "bold", styles: { bold: true } },
      { type: "text", text: " word", styles: {} },
    ]));
    expect(applyCommentStyle(editor, "c_2", "p1", "bold word")).toBe(true);
    const runs = editor.document[0]!.content as { text: string; styles: Record<string, unknown> }[];
    expect(runs.filter((r) => r.styles.comment === "c_2").map((r) => r.text).join("")).toBe("bold word");
  });

  it("only looks in the block's own text, not in its child blocks", () => {
    const editor = makeEditor([], [{
      id: "p1", type: "paragraph", content: "parent words",
      children: [{ id: "k1", type: "paragraph", content: "child has the quote" }],
    }]);
    expect(applyCommentStyle(editor, "c_1", "p1", "quote")).toBe(false);
    expect(applyCommentStyle(editor, "c_1", "k1", "quote")).toBe(true);
    const kid = (editor.document[0] as unknown as { children: { content: { text: string; styles: Record<string, unknown> }[] }[] }).children[0]!;
    expect(kid.content.filter((r) => r.styles.comment === "c_1").map((r) => r.text)).toEqual(["quote"]);
    const parent = editor.document[0]!.content as { styles: Record<string, unknown> }[];
    expect(parent.some((r) => r.styles.comment)).toBe(false);
  });

  it("marks the occurrence nearest the given position", () => {
    const editor = makeEditor([], para("the cat and the dog"));
    // block text starts at position 3; the second "the" is at index 12
    expect(applyCommentStyle(editor, "c_1", "p1", "the", 15)).toBe(true);
    const runs = editor.document[0]!.content as { text: string; styles: Record<string, unknown> }[];
    expect(runs.map((r) => [r.text, !!r.styles.comment])).toEqual([["the cat and ", false], ["the", true], [" dog", false]]);
  });

  it("returns false and leaves the text alone when the quote is gone", () => {
    const editor = makeEditor([], para("something else"));
    expect(applyCommentStyle(editor, "c_1", "p1", "this")).toBe(false);
    const runs = editor.document[0]!.content as { styles: Record<string, unknown> }[];
    expect(runs.some((r) => r.styles.comment)).toBe(false);
  });

  it("leaves another thread's words alone", () => {
    const editor = makeEditor([], para(commented));
    stripCommentStyle(editor, "c_other");
    const runs = editor.document[0]!.content as { styles: Record<string, unknown> }[];
    expect(runs.some((r) => r.styles.comment === "c_1")).toBe(true);
  });
});
