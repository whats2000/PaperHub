import { afterEach, describe, expect, it } from "vitest";

import { matchInlineMath, mathInput, slashLineText, turnIntoEquation } from "@/components/notes/mathInput";
import { cleanupEditors, makeEditor, press, type, type AnyEditor } from "@/components/notes/testEditor";

afterEach(cleanupEditors);

describe("matchInlineMath", () => {
  it.each([
    ["$x^2$", "x^2"],
    ["energy $E = mc^2$", "E = mc^2"],
    ["$a\\$b$", "a\\$b"],
  ])("takes %j", (text, latex) => {
    expect(matchInlineMath(text)?.latex).toBe(latex);
  });

  it.each([
    ["\\$x$", "an escaped opening $"],
    ["$x\\$", "an escaped closing $"],
    ["$$", "the block-equation start"],
    ["$$x$", "a $$ opening"],
    ["$$x$$", "display delimiters"],
    ["costs $5 and $", "a space before the closing $ (prices)"],
    ["$ x$", "a space after the opening $"],
    ["$$ ", "nothing between"],
  ])("leaves %j alone: %s", (text) => {
    expect(matchInlineMath(text)).toBeNull();
  });
});

const first = (editor: AnyEditor) =>
  (editor.document[0] as unknown as { content: { type: string; text?: string; props?: { latex: string } }[] })
    .content;

describe("typing math", () => {
  it("$x^2$ becomes inline math on the closing $", () => {
    const editor = makeEditor([mathInput]);
    type(editor, "so $x^2$ is");
    expect(first(editor).map((c) => (c.type === "inlineMath" ? `[${c.props!.latex}]` : c.text)).join("")).toBe(
      "so [x^2] is",
    );
  });

  it("prices stay text: $5 and $6", () => {
    const editor = makeEditor([mathInput]);
    type(editor, "costs $5 and $6 today");
    expect(first(editor)).toEqual([expect.objectContaining({ type: "text", text: "costs $5 and $6 today" })]);
  });

  it("an escaped \\$ stays literal", () => {
    const editor = makeEditor([mathInput]);
    type(editor, "\\$x$");
    expect(first(editor).some((c) => c.type === "inlineMath")).toBe(false);
  });

  it("$$ then Enter on an empty line makes a block equation", () => {
    const editor = makeEditor([mathInput]);
    type(editor, "$$");
    press(editor, { key: "Enter" });
    expect(editor.document[0]!.type).toBe("math");
    expect(editor.getTextCursorPosition().block.id).toBe(editor.document[0]!.id);
  });

  it("$$ then Enter after text on the line splits as usual", () => {
    const editor = makeEditor([mathInput]);
    type(editor, "a $$");
    press(editor, { key: "Enter" });
    expect(editor.document[0]!.type).toBe("paragraph");
  });
});

describe("turning a line into an equation (slash menu)", () => {
  it("reads the line without the /query and makes its text the LaTeX", () => {
    const editor = makeEditor([mathInput]);
    type(editor, "x^2 + 1 /eq");
    expect(slashLineText(editor, "eq")).toBe("x^2 + 1 ");
    expect(slashLineText(editor, "nothing")).toBe("x^2 + 1 /eq");
    editor.updateBlock(editor.document[0]!, { content: "x^2 + 1 " });
    turnIntoEquation(editor);
    expect(editor.document[0]!.type).toBe("math");
    expect((editor.document[0]!.props as { latex: string }).latex).toBe("x^2 + 1");
  });
});
