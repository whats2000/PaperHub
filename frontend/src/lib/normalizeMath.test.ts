import { describe, expect, it } from "vitest";

import { normalizeMath } from "@/lib/normalizeMath";

describe("normalizeMath lifts evidence markers out of math", () => {
  it("lifts chunk, figure, view and unsourced markers past the closing fence", () => {
    expect(normalizeMath("$a + b [chunk:3]$ end")).toBe("$a + b $ [chunk:3] end");
    expect(normalizeMath("$x = y [view:38/6/73,55,493,944]$")).toBe(
      "$x = y $ [view:38/6/73,55,493,944]",
    );
    expect(normalizeMath("$k [fig:20/fig-003]$")).toBe("$k $ [fig:20/fig-003]");
    expect(normalizeMath("$A = act(K) [unsourced:quote_not_found]$")).toBe(
      "$A = act(K) $ [unsourced:quote_not_found]",
    );
  });

  it("drops to the next line after display math", () => {
    expect(normalizeMath("$$\nA = B [view:1/2/0,0,10,10]\n$$")).toBe(
      "$$\nA = B \n$$\n[view:1/2/0,0,10,10]",
    );
  });
});
