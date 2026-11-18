import { describe, expect, it } from "vitest";

import { enterTracker } from "@/components/notes/noteSuggestions";

describe("enterTracker", () => {
  it("fades a suggestion in once, and forgets the ones no longer open", () => {
    let t = 1000;
    const seen = enterTracker(() => t);
    expect(seen.entering("s1")).toBe(true);
    expect(seen.entering("s2")).toBe(true);
    t += 10_000;
    expect(seen.entering("s1")).toBe(false);
    seen.keep(["s2"]);
    expect(seen.size()).toBe(1);
    seen.keep([]);
    expect(seen.size()).toBe(0);
  });
});
