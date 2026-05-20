import { describe, expect, it } from "vitest";
import { findAndHighlight, HIGHLIGHT_CLASS } from "@/lib/findAndHighlight";

function docFrom(html: string): Document {
  return new DOMParser().parseFromString(
    `<!DOCTYPE html><html><body>${html}</body></html>`,
    "text/html",
  );
}

describe("findAndHighlight", () => {
  it("finds text within a single text node and highlights it", () => {
    const doc = docFrom("<p>Expert collapse is mitigated by load balancing.</p>");
    const ok = findAndHighlight(doc, "Expert collapse is mitigated");
    expect(ok).toBe(true);
    expect(doc.querySelector(`.${HIGHLIGHT_CLASS}`)).not.toBeNull();
  });

  it("normalizes whitespace across the needle and the DOM", () => {
    const doc = docFrom("<p>Expert collapse\n   is   mitigated by balancing.</p>");
    const ok = findAndHighlight(doc, "Expert collapse is mitigated by balancing.");
    expect(ok).toBe(true);
  });

  it("matches on a long needle's prefix (rendering drops the tail)", () => {
    const doc = docFrom("<p>The router assigns tokens to experts.</p>");
    const longNeedle =
      "The router assigns tokens to experts. " +
      "Then $\\mathcal{L}$ regularizes — math the renderer mangled.";
    expect(findAndHighlight(doc, longNeedle)).toBe(true);
  });

  it("returns false when the passage is absent", () => {
    const doc = docFrom("<p>Completely unrelated content.</p>");
    expect(findAndHighlight(doc, "this text does not appear anywhere")).toBe(false);
  });

  it("removes a prior highlight before adding a new one", () => {
    const doc = docFrom("<p>alpha bravo charlie delta echo foxtrot.</p>");
    findAndHighlight(doc, "alpha bravo");
    findAndHighlight(doc, "charlie delta");
    expect(doc.querySelectorAll(`.${HIGHLIGHT_CLASS}`).length).toBe(1);
  });
});
