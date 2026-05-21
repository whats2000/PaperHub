import { describe, expect, it } from "vitest";
import {
  buildChunkOrdinalMap,
  CHUNK_MARKER_RE,
  parseChunkIds,
} from "@/lib/chunkCitations";

describe("buildChunkOrdinalMap", () => {
  it("assigns ordinals in first-appearance order", () => {
    const m = buildChunkOrdinalMap("a[chunk:50]b[chunk:12]c");
    expect(m.get(50)).toBe(1);
    expect(m.get(12)).toBe(2);
  });

  it("dedupes: a re-cited chunk reuses its ordinal", () => {
    const m = buildChunkOrdinalMap("[chunk:7] then [chunk:9] then [chunk:7]");
    expect(m.get(7)).toBe(1);
    expect(m.get(9)).toBe(2);
    expect(m.size).toBe(2);
  });

  it("returns an empty map when there are no markers", () => {
    expect(buildChunkOrdinalMap("no citations here").size).toBe(0);
  });

  it("CHUNK_MARKER_RE matches [chunk:<digits>] globally", () => {
    const matches = [...":a[chunk:1]b[chunk:23]".matchAll(CHUNK_MARKER_RE)];
    expect(matches.map((x) => x[1])).toEqual(["1", "23"]);
  });

  it("matches multi-id markers, with or without spaces", () => {
    const m = buildChunkOrdinalMap("see [chunk:75143, 75161] and [chunk:12,75143]");
    expect(m.get(75143)).toBe(1);
    expect(m.get(75161)).toBe(2);
    expect(m.get(12)).toBe(3);
    // 75143 re-cited in the second marker keeps its ordinal
    expect(m.size).toBe(3);
  });

  it("parseChunkIds splits an id list and trims whitespace", () => {
    expect(parseChunkIds("75143, 75161")).toEqual([75143, 75161]);
    expect(parseChunkIds("12,13,14")).toEqual([12, 13, 14]);
    expect(parseChunkIds("9")).toEqual([9]);
  });
});
