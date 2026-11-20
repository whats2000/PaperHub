import { describe, expect, it } from "vitest";

import { INLINE_CITE_MAX_GRAPHEMES, graphemeCount, isShortQuote } from "./markCiteText";

describe("isShortQuote", () => {
  it("is at most 24 user-perceived characters after trimming", () => {
    expect(INLINE_CITE_MAX_GRAPHEMES).toBe(24);
    expect(isShortQuote("  62%  ")).toBe(true);
    expect(isShortQuote("x".repeat(24))).toBe(true);
    expect(isShortQuote("x".repeat(25))).toBe(false);
    expect(isShortQuote("   ")).toBe(false);
  });

  it("counts CJK, combining marks and emoji as a reader sees them", () => {
    expect(graphemeCount("治療組反應率")).toBe(6);
    expect(graphemeCount("é")).toBe(1);
    expect(graphemeCount("👨‍👩‍👧")).toBe(1);
    expect(isShortQuote("中".repeat(24))).toBe(true);
    expect(isShortQuote("中".repeat(25))).toBe(false);
    // 24 emoji are 48 UTF-16 units, still short.
    expect(isShortQuote("😀".repeat(24))).toBe(true);
  });
});
