import { afterEach, describe, expect, it } from "vitest";

import { insertMention, mentionHref, parseMentionHref, quoteTargets, toggleQuote } from "@/components/notes/formatActions";
import { FORMAT_BUTTONS, formatHint } from "@/components/notes/formatButtons";
import { cleanupEditors, makeEditor } from "@/components/notes/testEditor";

afterEach(cleanupEditors);

describe("formatting bar tooltips", () => {
  it("lists B I U S, quote, code, hide, Link and Comment in that order", () => {
    expect(FORMAT_BUTTONS.map((b) => b.id)).toEqual([
      "bold", "italic", "underline", "strike", "quote", "code", "hidden", "link", "comment",
    ]);
  });

  it.each([
    ["bold", "**text** · Ctrl+B"],
    ["italic", "*text* or _text_ · Ctrl+I"],
    ["underline", "__text__ · Ctrl+U"],
    ["strike", "~~text~~ · Ctrl+Shift+S"],
    ["quote", '" at the line start'],
    ["code", "`text` · Ctrl+E"],
    ["hidden", "||text||"],
    ["link", "@"],
    ["comment", "Ctrl+Shift+M"],
  ] as const)("%s names its Markdown and key", (id, hint) => {
    expect(formatHint(id)).toBe(hint);
  });

  it("puts the prose words through the translator and leaves Markdown and keys literal", () => {
    const tr = (key: string) => `<${key}>`;
    expect(formatHint("quote", tr)).toBe('" <notes:format.atLineStart>');
    expect(formatHint("italic", tr)).toBe("*text* <notes:format.or> _text_ · Ctrl+I");
  });
});

const refBlock = {
  id: "q1",
  type: "paperQuote",
  props: { markId: "m", color: "green", kind: "highlight", quote: "kept", pieces: [], source: {} },
};
const types = (e: { document: { type: string }[] }) => e.document.map((b) => b.type);

describe("Quote button", () => {
  const doc = () => [
    { id: "p1", type: "paragraph", content: "one" },
    refBlock,
    { id: "c1", type: "codeBlock", props: { language: "text" }, content: "code" },
    { id: "p2", type: "paragraph", content: "two" },
  ];

  it("only offers blocks that hold text", () => {
    const editor = makeEditor([], doc());
    expect(quoteTargets(editor.document).map((b) => b.id)).toEqual(["p1", "p2"]);
  });

  it("turns the text blocks of a selection into quotes and leaves references and code alone", () => {
    const editor = makeEditor([], doc());
    editor.setSelection("p1", "p2");
    toggleQuote(editor);
    expect(types(editor)).toEqual(["quote", "paperQuote", "codeBlock", "quote"]);
    expect((editor.document[1] as unknown as { props: { quote: string } }).props.quote).toBe("kept");
    editor.setSelection("p1", "p2");
    toggleQuote(editor);
    expect(types(editor)).toEqual(["paragraph", "paperQuote", "codeBlock", "paragraph"]);
  });
});

describe("Link with a selection", () => {
  const props = { kind: "paper" as const, paperId: 7, sectionKey: "", label: "Attention" };
  type Inline = { type: string; href?: string; text?: string; content?: { text: string }[] };

  it("keeps the selected words and links them", () => {
    const editor = makeEditor([], [{ id: "p1", type: "paragraph", content: "see the attention paper" }]);
    let from = 0;
    editor._tiptapEditor.state.doc.descendants((node, pos) => {
      if (node.isText && node.text!.includes("attention")) from = pos + node.text!.indexOf("attention");
    });
    editor._tiptapEditor.commands.setTextSelection({ from, to: from + "attention".length });
    insertMention(editor, props);
    const content = (editor.document[0] as unknown as { content: Inline[] }).content;
    const link = content.find((c) => c.type === "link")!;
    expect(link.content!.map((c) => c.text).join("")).toBe("attention");
    expect(link.href).toBe("paperhub://paper/7");
    expect(content.map((c) => (c.type === "text" ? c.text : "")).join("")).toBe("see the  paper");
  });

  it("without a selection inserts the chip", () => {
    const editor = makeEditor([], [{ id: "p1", type: "paragraph", content: "x" }]);
    editor.setTextCursorPosition("p1", "end");
    insertMention(editor, props);
    const content = (editor.document[0] as unknown as { content: Inline[] }).content;
    expect(content.some((c) => c.type === "mention")).toBe(true);
  });
});

describe("paper links", () => {
  it("round-trips a paper and a section href, and ignores other links", () => {
    expect(parseMentionHref(mentionHref({ paperId: 7, sectionKey: "" }))).toEqual({ paperId: 7, sectionKey: "" });
    expect(parseMentionHref(mentionHref({ paperId: 7, sectionKey: "2 Method" }))).toEqual({
      paperId: 7,
      sectionKey: "2 Method",
    });
    expect(parseMentionHref("https://example.com")).toBeNull();
  });
});
