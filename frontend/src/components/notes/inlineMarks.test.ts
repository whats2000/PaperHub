import { afterEach, describe, expect, it } from "vitest";

import { INLINE_RULES, matchInlineRule } from "@/components/notes/inlineMarks";
import { cleanupEditors, makeEditor, press, runs, type, type AnyEditor } from "@/components/notes/testEditor";

afterEach(cleanupEditors);

describe("INLINE_RULES (pure matching)", () => {
  it.each([
    ["**bold**", "bold", "bold"],
    ["*it*", "italic", "it"],
    ["_it_", "italic", "it"],
    ["__under__", "underline", "under"],
    ["~~gone~~", "strike", "gone"],
    ["`code`", "code", "code"],
    ["||secret||", "hidden", "secret"],
    ["say **hi there**", "bold", "hi there"],
  ])("%s becomes %s on the closing mark", (typed, style, content) => {
    const hit = matchInlineRule(typed);
    expect(hit?.rule.style).toBe(style);
    expect(hit?.content).toBe(content);
  });

  it.each([
    ["my_var_name"],
    ["snake_case_"],
    ["2*3*"],
    ["**b*"],
    ["**b"],
    ["* spaced *"],
    ["_ spaced _"],
    ["a __b_"],
  ])("leaves %s alone", (typed) => {
    expect(matchInlineRule(typed)).toBeNull();
  });

  it("names every mark the brief lists", () => {
    expect(INLINE_RULES.map((r) => r.style).sort()).toEqual(
      ["bold", "code", "hidden", "italic", "italic", "strike", "underline"].sort(),
    );
  });
});

describe("inline marks in the editor", () => {
  it.each([
    ["**bold**", { bold: true }, "bold"],
    ["*it*", { italic: true }, "it"],
    ["_it_", { italic: true }, "it"],
    ["__under__", { underline: true }, "under"],
    ["~~gone~~", { strike: true }, "gone"],
    ["`code`", { code: true }, "code"],
    ["||secret||", { hidden: true }, "secret"],
  ])("typing %s marks the text and drops the marks", (typed, styles, text) => {
    const editor = makeEditor();
    type(editor, typed);
    expect(runs(editor)).toEqual([{ text, styles }]);
  });

  it("keeps typing after a mark unmarked", () => {
    const editor = makeEditor();
    type(editor, "**b** and");
    expect(runs(editor)).toEqual([
      { text: "b", styles: { bold: true } },
      { text: " and", styles: {} },
    ]);
  });

  it.each([["my_var_name"], ["2*3*4"], ["**b*"], ["snake_case"]])("never converts %s", (typed) => {
    const editor = makeEditor();
    type(editor, typed);
    expect(runs(editor)).toEqual([{ text: typed, styles: {} }]);
  });

  it("does not convert inside inline code", () => {
    const editor = makeEditor();
    editor.insertInlineContent([{ type: "text", text: "x", styles: { code: true } }]);
    type(editor, "**b**");
    expect(runs(editor).some((r) => r.styles.bold)).toBe(false);
  });
});

describe("code blocks", () => {
  const first = (editor: AnyEditor) =>
    editor.document[0] as unknown as { type: string; props: { language: string } };

  it("```python + space starts a Python code block", () => {
    const editor = makeEditor();
    type(editor, "```python ");
    expect(first(editor).type).toBe("codeBlock");
    expect(first(editor).props.language).toBe("python");
  });

  it("```python + Enter starts one too", () => {
    const editor = makeEditor();
    type(editor, "```python");
    press(editor, { key: "Enter" });
    expect(first(editor).type).toBe("codeBlock");
    expect(first(editor).props.language).toBe("python");
  });

  it("an unknown language is plain text, not a crash", () => {
    const editor = makeEditor();
    type(editor, "```nosuchlang ");
    expect(first(editor).type).toBe("codeBlock");
    expect(first(editor).props.language).toBe("text");
  });

  it("Ctrl+Enter leaves the block for a new line below", () => {
    const editor = makeEditor();
    type(editor, "```python ");
    type(editor, "x = 1");
    press(editor, { key: "Enter", ctrlKey: true });
    expect(editor.document.map((b) => b.type)).toEqual(["codeBlock", "paragraph"]);
    expect(editor.getTextCursorPosition().block.type).toBe("paragraph");
  });
});
