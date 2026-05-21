import { describe, expect, it } from "vitest";
import { locatePassage } from "@/lib/pdfHighlight";

describe("locatePassage", () => {
  it("finds a passage spanning multiple items on the right page", () => {
    const pages = [
      ["Introductory text here."],
      ["Expert collapse", "is mitigated", "by load balancing across experts."],
    ];
    const m = locatePassage(
      pages,
      "Expert collapse is mitigated by load balancing across experts.",
    );
    expect(m?.pageNumber).toBe(2);
    expect([...(m?.itemIndexes ?? [])].sort((a, b) => a - b)).toEqual([0, 1, 2]);
  });

  it("returns null when the passage is absent", () => {
    const m = locatePassage(
      [["completely unrelated content"]],
      "this passage does not appear anywhere at all in the document text",
    );
    expect(m).toBeNull();
  });

  it("locates via the prefix when the tail is mangled (math)", () => {
    const pages = [["The router assigns tokens to experts."]];
    const needle =
      "The router assigns tokens to experts. " +
      "Then $\\mathcal{L}$ regularizes — math the extractor mangled.";
    const m = locatePassage(pages, needle);
    expect(m?.pageNumber).toBe(1);
    expect(m?.itemIndexes.has(0)).toBe(true);
  });

  it("highlights the full chunk extent, not just the matched prefix", () => {
    // One page, several items. The needle is long; the prefix (≤150 chars)
    // matches at item 0, but the full extent should reach later items too.
    const pages = [
      [
        "Load balancing prevents expert collapse in mixture-of-experts models.",
        " The auxiliary loss penalizes imbalance.",
        " Unrelated trailing sentence far away.",
      ],
    ];
    const needle =
      "Load balancing prevents expert collapse in mixture-of-experts models. " +
      "The auxiliary loss penalizes imbalance.";
    const m = locatePassage(pages, needle);
    expect(m?.pageNumber).toBe(1);
    // covers item 0 AND item 1 (the full chunk), but not the unrelated item 2
    expect(m?.itemIndexes.has(0)).toBe(true);
    expect(m?.itemIndexes.has(1)).toBe(true);
    expect(m?.itemIndexes.has(2)).toBe(false);
  });

  it("matches a short chunk only in full", () => {
    const pages = [["load balancing helps."]];
    expect(locatePassage(pages, "load balancing helps.")?.pageNumber).toBe(1);
    expect(locatePassage(pages, "load balancing rocks.")).toBeNull();
  });
});
