import { describe, expect, it } from "vitest";
import { CHANGELOG, localizedHighlights } from "@/lib/changelog";

describe("changelog loader", () => {
  it("exposes newest-first entries", () => {
    expect(CHANGELOG[0]!.version).toBe("2.37.1");
  });

  it("returns locale highlights, falling back to en", () => {
    const entry = CHANGELOG[0]!;
    expect(localizedHighlights(entry, "ja").length).toBeGreaterThan(0);
    // An unknown locale falls back to en.
    expect(localizedHighlights(entry, "fr")).toEqual(entry.highlights.en);
  });
});
