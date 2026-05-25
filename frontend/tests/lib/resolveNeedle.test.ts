/**
 * F2.1 Addendum A1 — resolveNeedle tests
 *
 * Verifies that the citation resolver prefers the clean `match_text` over the
 * raw `text` (which may contain markdown markers that break text-layer matching).
 */
import { describe, expect, it } from "vitest";
import { resolveNeedle } from "@/lib/resolveNeedle";
import { locatePassage } from "@/lib/pdfHighlight";
import { findAndHighlight } from "@/lib/findAndHighlight";

// A minimal chunk shape that mirrors ChunkResolution (only the fields we need here).
interface MinimalChunk {
  text: string;
  match_text?: string | null;
}

// ---------------------------------------------------------------------------
// resolveNeedle helper
// ---------------------------------------------------------------------------

describe("resolveNeedle", () => {
  it("returns match_text when present and non-null", () => {
    const chunk: MinimalChunk = {
      text: "## Model Architecture\n\nThe Transformer uses self-attention.",
      match_text: "Model Architecture The Transformer uses self-attention.",
    };
    expect(resolveNeedle(chunk)).toBe(chunk.match_text);
  });

  it("falls back to text when match_text is null", () => {
    const chunk: MinimalChunk = {
      text: "Expert collapse is mitigated by load balancing.",
      match_text: null,
    };
    expect(resolveNeedle(chunk)).toBe(chunk.text);
  });

  it("falls back to text when match_text is undefined (non-Marker chunk)", () => {
    const chunk: MinimalChunk = {
      text: "Expert collapse is mitigated by load balancing.",
    };
    expect(resolveNeedle(chunk)).toBe(chunk.text);
  });

  it("returns an empty-string match_text as-is (not the text)", () => {
    // An empty string is technically defined — return it rather than falling back.
    const chunk: MinimalChunk = {
      text: "Some text.",
      match_text: "",
    };
    // Empty string is falsy → falls back to text (match_text ?? text behaviour).
    // The `??` operator only falls back on null/undefined, so "" is returned as-is.
    expect(resolveNeedle(chunk)).toBe("");
  });
});

// ---------------------------------------------------------------------------
// Integration: locatePassage picks the right page when using resolveNeedle
// ---------------------------------------------------------------------------

describe("resolveNeedle + locatePassage integration", () => {
  const pages = [
    ["Unrelated page content."],
    [
      "Model Architecture",
      "The Transformer uses self-attention",
      "mechanisms for sequence transduction.",
    ],
  ];

  it("SUCCEEDS when using match_text (clean form)", () => {
    const chunk: MinimalChunk = {
      text: "## Model Architecture\n\nThe Transformer uses self-attention mechanisms for sequence transduction.",
      match_text: "Model Architecture The Transformer uses self-attention mechanisms for sequence transduction.",
    };
    const needle = resolveNeedle(chunk);
    const match = locatePassage(pages, needle);
    expect(match).not.toBeNull();
    expect(match?.pageNumber).toBe(2);
  });

  it("FAILS when using raw markdown text (proves the fix matters)", () => {
    const chunk: MinimalChunk = {
      text: "## Model Architecture\n\nThe Transformer uses self-attention mechanisms for sequence transduction.",
      match_text: "Model Architecture The Transformer uses self-attention mechanisms for sequence transduction.",
    };
    // Deliberately pass the raw markdown text — must NOT find a match.
    const match = locatePassage(pages, chunk.text);
    expect(match).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Integration: findAndHighlight picks the right element when using resolveNeedle
// ---------------------------------------------------------------------------

function docFrom(html: string): Document {
  return new DOMParser().parseFromString(
    `<!DOCTYPE html><html><body>${html}</body></html>`,
    "text/html",
  );
}

describe("resolveNeedle + findAndHighlight integration", () => {
  const docHtml = "<h2>Model Architecture</h2><p>The Transformer uses self-attention.</p>";

  it("SUCCEEDS when using match_text (clean form)", () => {
    const doc = docFrom(docHtml);
    const chunk: MinimalChunk = {
      text: "## Model Architecture\n\nThe Transformer uses self-attention.",
      match_text: "Model Architecture The Transformer uses self-attention.",
    };
    const needle = resolveNeedle(chunk);
    expect(findAndHighlight(doc, needle)).toBe(true);
  });

  it("FAILS when using raw markdown text (proves the fix matters)", () => {
    const doc = docFrom(docHtml);
    const chunk: MinimalChunk = {
      text: "## Model Architecture\n\nThe Transformer uses self-attention.",
      match_text: "Model Architecture The Transformer uses self-attention.",
    };
    // The raw markdown needle contains "##" and "\n\n" which won't appear in the
    // rendered HTML text layer.
    expect(findAndHighlight(doc, chunk.text)).toBe(false);
  });

  it("falls back to text when match_text is absent (non-Marker chunk)", () => {
    const doc = docFrom("<p>Expert collapse is mitigated by load balancing.</p>");
    const chunk: MinimalChunk = {
      text: "Expert collapse is mitigated by load balancing.",
      match_text: null,
    };
    const needle = resolveNeedle(chunk);
    expect(findAndHighlight(doc, needle)).toBe(true);
  });
});
