import { describe, expect, it } from "vitest";

import { htmlPlace, joinedQuote, joinTarget, livePieceIndexes, pdfPlace, readingOrder, type PenMark } from "@/lib/marks/pieces";
import type { MarkPiece } from "@/types/notes";

const session: PenMark[] = [
  { id: "mk_a", color: "green", places: ["h:1"] },
  { id: "mk_b", color: "blue", places: ["h:1"] },
  { id: "mk_c", color: "green", places: ["h:2", "h:3"] },
];

describe("joinTarget", () => {
  it("joins a stroke to the session's mark of the same colour in the same paragraph", () => {
    expect(joinTarget(session, { color: "green", place: "h:1" })).toBe("mk_a");
    expect(joinTarget(session, { color: "blue", place: "h:1" })).toBe("mk_b");
    // A mark whose stroke crossed paragraphs is in each of them.
    expect(joinTarget(session, { color: "green", place: "h:3" })).toBe("mk_c");
  });

  it("never joins across colours, paragraphs, or without a place", () => {
    expect(joinTarget(session, { color: "pink", place: "h:1" })).toBeNull();
    expect(joinTarget(session, { color: "blue", place: "h:2" })).toBeNull();
    expect(joinTarget(session, { color: "green", place: null })).toBeNull();
    // Turning the pen off ends the session: nothing to join.
    expect(joinTarget([], { color: "green", place: "h:1" })).toBeNull();
  });

  it("takes the newest of several matching marks", () => {
    const two: PenMark[] = [...session, { id: "mk_d", color: "green", places: ["h:1"] }];
    expect(joinTarget(two, { color: "green", place: "h:1" })).toBe("mk_d");
  });
});

describe("places", () => {
  it("names a paragraph element the same way each time, and two elements apart", () => {
    const a = document.createElement("p");
    const b = document.createElement("p");
    expect(htmlPlace(a)).toBe(htmlPlace(a));
    expect(htmlPlace(a)).not.toBe(htmlPlace(b));
  });

  it("names a PDF piece by its page and Marker block; none without a block", () => {
    expect(pdfPlace(2, "/page/2/Text/4")).toBe("p:2:/page/2/Text/4");
    expect(pdfPlace(2, null)).toBeNull();
  });
});

describe("pieces", () => {
  const pieces: MarkPiece[] = [
    { quote: "first words" },
    { quote: "gone", erased: true },
    { quote: "split away", removed: true },
    { quote: "last words" },
  ];

  it("lists the pieces still on the paper", () => {
    expect(livePieceIndexes(pieces)).toEqual([0, 3]);
  });

  it("quotes a mark of several pieces with … between them", () => {
    expect(joinedQuote(pieces)).toBe("first words … last words");
    expect(joinedQuote([{ quote: "only" }])).toBe("only");
  });
});

describe("reading order (shown only; stored pieces keep stroke order)", () => {
  it("reads HTML pieces by chunk, then offset in the reading text", () => {
    const later = { quote: "ahead of time", chunk_start: 4, text_start: 80 };
    const earlier = { quote: "We predict", chunk_start: 4, text_start: 10 };
    const before = { quote: "Robots", chunk_start: 3, text_start: 900 };
    expect(readingOrder([later, earlier, before]).map((p) => p.quote)).toEqual(["Robots", "We predict", "ahead of time"]);
    expect(joinedQuote([later, earlier])).toBe("We predict … ahead of time");
  });

  it("reads PDF pieces by page, then top, then left", () => {
    const lower = { quote: "lower", page: 0, rects: [[0.1, 0.5, 0.3, 0.52]] };
    const upper = { quote: "upper", page: 0, rects: [[0.1, 0.2, 0.3, 0.22]] };
    const next = { quote: "page one", page: 1, box: [0.1, 0.1, 0.3, 0.12] as [number, number, number, number] };
    expect(joinedQuote([next, lower, upper])).toBe("upper … lower … page one");
  });
});
