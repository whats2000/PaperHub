import { describe, expect, it } from "vitest";

import { FORMAT_BUTTONS, formatHint } from "@/components/notes/formatButtons";

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
    ["code", "`text` · Ctrl+E"],
    ["hidden", "||text||"],
    ["link", "@"],
    ["comment", "Ctrl+Shift+M"],
  ] as const)("%s names its Markdown and key", (id, hint) => {
    expect(formatHint(id)).toBe(hint);
  });
});
