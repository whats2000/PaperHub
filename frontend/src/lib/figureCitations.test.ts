import { describe, expect, it } from "vitest";

import { NOTE_MARKER_RE, parseNoteIds } from "@/lib/figureCitations";

function markers(text: string): string[] {
  return [...text.matchAll(new RegExp(NOTE_MARKER_RE.source, "g"))].map((m) => m[1] ?? "");
}

describe("NOTE_MARKER_RE", () => {
  it("parses [note:b_x]", () => {
    const [raw] = markers("Your note says so [note:b_AbC123xYz9].");
    expect(raw).toBe("b_AbC123xYz9");
    expect(parseNoteIds(raw ?? "")).toEqual(["b_AbC123xYz9"]);
  });

  it("parses lists in both spellings", () => {
    const found = markers("a [note:b_A1, b_B2] and [note:b_C3, note:b_D4]");
    expect(found.flatMap(parseNoteIds)).toEqual(["b_A1", "b_B2", "b_C3", "b_D4"]);
  });

  it("ignores chunk, figure and unsourced markers", () => {
    expect(markers("[chunk:3] [fig:1/f] [unsourced:note_not_read]")).toEqual([]);
  });
});
