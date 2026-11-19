import { describe, expect, it } from "vitest";

import { defaultNoteTitle, noteDisplayTitle } from "./title";

describe("a note's display title", () => {
  it("is the user's title, else the default title, else Untitled", () => {
    expect(noteDisplayTitle({ note: { title: "Mine" }, defaultTitle: "Paper" })).toBe("Mine");
    expect(noteDisplayTitle({ note: { title: " " }, defaultTitle: "Paper" })).toBe("Paper");
    expect(noteDisplayTitle({ note: { title: "" }, defaultTitle: "" })).toBe("Untitled");
    expect(defaultNoteTitle({ note: { title: "Mine" }, defaultTitle: "Paper" })).toBe("Paper");
  });
});
