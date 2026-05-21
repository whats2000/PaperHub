import { describe, expect, it } from "vitest";
import {
  findAndHighlight,
  highlightChunkRange,
  scrollToSection,
  HIGHLIGHT_CLASS,
} from "@/lib/findAndHighlight";

function docFrom(html: string): Document {
  return new DOMParser().parseFromString(
    `<!DOCTYPE html><html><body>${html}</body></html>`,
    "text/html",
  );
}

describe("highlightChunkRange", () => {
  it("wraps the chunk's text in a span and returns true", () => {
    const doc = docFrom(
      '<p><span id="phchunk-0"></span>Expert collapse is mitigated.</p>',
    );
    expect(highlightChunkRange(doc, "phchunk-0")).toBe(true);
    const hl = doc.querySelector(`.${HIGHLIGHT_CLASS}`);
    expect(hl?.tagName).toBe("SPAN");
    expect(hl?.textContent).toBe("Expert collapse is mitigated.");
  });

  it("returns false when the anchor is absent (caller falls back to text-search)", () => {
    const doc = docFrom("<p>no anchor here</p>");
    expect(highlightChunkRange(doc, "phchunk-9")).toBe(false);
    expect(doc.querySelector(`.${HIGHLIGHT_CLASS}`)).toBeNull();
  });
});

describe("highlightChunkRange — full chunk between sentinels", () => {
  it("wraps every text node from this sentinel up to the next, across blocks", () => {
    const doc = docFrom(
      '<p><span id="phchunk-0"></span>First paragraph.</p>' +
        "<p>Middle paragraph.</p>" +
        '<p><span id="phchunk-1"></span>Next chunk starts here.</p>',
    );
    expect(highlightChunkRange(doc, "phchunk-0")).toBe(true);
    const text = Array.from(doc.querySelectorAll(`.${HIGHLIGHT_CLASS}`))
      .map((m) => m.textContent)
      .join("|");
    expect(text).toContain("First paragraph.");
    expect(text).toContain("Middle paragraph."); // spans across blocks
    expect(text).not.toContain("Next chunk"); // stops before the next sentinel
  });

  it("uses the next sentinel in document order even when ordinals have gaps", () => {
    // phchunk-1 was skipped at ingest (sentinel landed in math); the next
    // existing anchor is phchunk-2, and that must bound the highlight.
    const doc = docFrom(
      '<p><span id="phchunk-0"></span>Chunk zero body.</p>' +
        '<p><span id="phchunk-2"></span>Chunk two body.</p>',
    );
    expect(highlightChunkRange(doc, "phchunk-0")).toBe(true);
    const text = Array.from(doc.querySelectorAll(`.${HIGHLIGHT_CLASS}`))
      .map((m) => m.textContent)
      .join("|");
    expect(text).toContain("Chunk zero body.");
    expect(text).not.toContain("Chunk two body.");
  });
});

describe("scrollToSection", () => {
  it("matches a heading by title (loose) and highlights it", () => {
    const doc = docFrom("<h2>3.2 Expert Routing</h2><p>body</p>");
    expect(scrollToSection(doc, "Expert Routing")).toBe(true);
    expect(doc.querySelector(`.${HIGHLIGHT_CLASS}`)?.tagName).toBe("H2");
  });

  it("returns false when no heading matches", () => {
    const doc = docFrom("<h1>Introduction</h1><p>body</p>");
    expect(scrollToSection(doc, "Conclusions")).toBe(false);
  });
});

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

  it("does NOT match an unrelated paragraph via a short generic prefix", () => {
    const doc = docFrom(
      "<p>Introduction to the topic and motivation.</p>" +
        "<p>The model architecture uses attention layers extensively.</p>",
    );
    // A stored chunk that shares only a short generic opener with paragraph 2,
    // then diverges. Must NOT highlight the wrong paragraph.
    const needle =
      "The model architecture is a totally different sentence that does not " +
      "exist in the rendered document anywhere here at all nope none whatsoever.";
    const ok = findAndHighlight(doc, needle);
    expect(ok).toBe(false);
    expect(doc.querySelector(`.${HIGHLIGHT_CLASS}`)).toBeNull();
  });

  it("a short needle must match in full (no sub-floor)", () => {
    const doc = docFrom("<p>load balancing helps.</p>");
    expect(findAndHighlight(doc, "load balancing helps.")).toBe(true);
    expect(findAndHighlight(doc, "load balancing rocks.")).toBe(false);
  });

  it("injects a stylesheet rule so the highlight is visible inside the iframe doc", () => {
    const doc = docFrom("<p>Expert collapse is mitigated by load balancing.</p>");
    const ok = findAndHighlight(doc, "Expert collapse is mitigated");
    expect(ok).toBe(true);
    const style = doc.getElementById("ph-cite-hl-style");
    expect(style).not.toBeNull();
    expect(style?.textContent).toContain(`.${HIGHLIGHT_CLASS}`);
    expect(style?.textContent).toMatch(/background/i);
  });

  it("injects the stylesheet only once across repeated calls", () => {
    const doc = docFrom("<p>alpha bravo charlie delta echo foxtrot.</p>");
    findAndHighlight(doc, "alpha bravo");
    findAndHighlight(doc, "charlie delta");
    expect(doc.querySelectorAll("#ph-cite-hl-style").length).toBe(1);
  });
});
