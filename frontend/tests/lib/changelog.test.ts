import { describe, expect, it } from "vitest";
import { CHANGELOG, localizedHighlights } from "@/lib/changelog";

describe("changelog loader", () => {
  // Anchored to the app version (Vite `define`, sourced from package.json) rather
  // than a literal: merge-prep bumps package.json and prepends the changelog entry
  // in the same release, so a hardcoded version silently goes stale between them.
  it("leads with the shipped app version", () => {
    expect(CHANGELOG[0]!.version).toBe(__APP_VERSION__);
  });

  it("returns locale highlights, falling back to en", () => {
    const entry = CHANGELOG[0]!;
    expect(localizedHighlights(entry, "ja").length).toBeGreaterThan(0);
    // An unknown locale falls back to en.
    expect(localizedHighlights(entry, "fr")).toEqual(entry.highlights.en);
  });
});
