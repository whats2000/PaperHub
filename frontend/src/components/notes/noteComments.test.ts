import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { commentKeys, START_COMMENT_EVENT, stripCommentStyle } from "@/components/notes/noteComments";
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

  it("is taken off by id (other styles kept) and put back by the returned function", () => {
    const editor = makeEditor([], para(commented));
    const restore = stripCommentStyle(editor, "c_1");
    const after = editor.document[0]!.content as { text: string; styles: Record<string, unknown> }[];
    expect(after.some((r) => r.styles.comment)).toBe(false);
    expect(after.find((r) => r.text === "this")!.styles.bold).toBe(true);
    restore();
    const back = editor.document[0]!.content as { text: string; styles: Record<string, unknown> }[];
    expect(back.find((r) => r.text === "this")!.styles.comment).toBe("c_1");
  });

  it("leaves another thread's words alone", () => {
    const editor = makeEditor([], para(commented));
    stripCommentStyle(editor, "c_other");
    const runs = editor.document[0]!.content as { styles: Record<string, unknown> }[];
    expect(runs.some((r) => r.styles.comment === "c_1")).toBe(true);
  });
});
