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

  it("parses the ids of blocks made in the editor (UUIDs)", () => {
    const u = "5a070443-97b8-40ae-bbcc-ff555ee02d52";
    const found = markers(`You doubted it [note:${u}, note:b_A1].`);
    expect(found.flatMap(parseNoteIds)).toEqual([u, "b_A1"]);
  });

  it("parses any id shape the server stores (letters, digits, _ and -)", () => {
    expect(markers("So [note:blk-7_x].").flatMap(parseNoteIds)).toEqual(["blk-7_x"]);
    expect(markers(`[note:${"x".repeat(65)}]`)).toEqual([]);
  });

  it("ignores chunk, figure and unsourced markers", () => {
    expect(markers("[chunk:3] [fig:1/f] [unsourced:note_not_read]")).toEqual([]);
  });
});
