import { describe, expect, it } from "vitest";

import {
  buildTextIndex,
  chunkMap,
  findPiece,
  groupBySection,
  normaliseText,
  selectionToPieces,
} from "@/lib/marks/anchor";
// Shared with the backend's re-anchor (notes/html_text.py reads the same file).
import ruleHtml from "@/lib/__fixtures__/mark_text/rule.html?raw";
import ruleExpected from "@/lib/__fixtures__/mark_text/rule.expected.json?raw";

function makeDoc(body: string): Document {
  const doc = document.implementation.createHTMLDocument("paper");
  doc.body.innerHTML = body;
  return doc;
}

const PAPER = `
  <h2>1 Introduction</h2>
  <p><span id="phchunk-0"></span>Robots learn from demonstrations. They often fail
     when the scene changes, which motivates our method.</p>
  <h2>2 Method</h2>
  <p><span id="phchunk-1"></span>We predict the agent pose ahead of time.
     <span id="phchunk-2"></span>The latency drops by 89% on all trajectories.</p>
  <div class="ph-tr" data-open><div class="ph-tr-inner">翻譯</div></div>
  <p>The latency drops by 89% on all trajectories.</p>
`;

const CHUNKS = chunkMap([
  { id: 101, dom_id: "phchunk-0", section: "1 Introduction" },
  { id: 102, dom_id: "phchunk-1", section: "2 Method" },
  { id: 103, dom_id: "phchunk-2", section: "2 Method" },
]);

/** A range over `doc`'s text from the first occurrence of `from` to the end
 * of the first occurrence of `to` after it. */
function rangeOver(doc: Document, from: string, to: string): Range {
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text);
  const range = doc.createRange();
  let started = false;
  for (const n of nodes) {
    if (!started) {
      const i = n.data.indexOf(from);
      if (i < 0) continue;
      range.setStart(n, i);
      started = true;
      const j = n.data.indexOf(to, i);
      if (j >= 0) {
        range.setEnd(n, j + to.length);
        return range;
      }
      continue;
    }
    const j = n.data.indexOf(to);
    if (j >= 0) {
      range.setEnd(n, j + to.length);
      return range;
    }
  }
  throw new Error(`not found: ${from} … ${to}`);
}

describe("selectionToPieces", () => {
  it("makes one piece per paragraph for a selection across two", () => {
    const doc = makeDoc(PAPER);
    const pieces = selectionToPieces(doc, rangeOver(doc, "which motivates", "agent pose"), CHUNKS);
    expect(pieces).toHaveLength(2);
    expect(pieces[0]!.quote).toBe("which motivates our method.");
    expect(pieces[1]!.quote).toBe("We predict the agent pose");
    expect(pieces[0]).toMatchObject({ chunk_start: 101, chunk_end: 101 });
    expect(pieces[1]).toMatchObject({ chunk_start: 102, chunk_end: 102 });
  });

  it("takes the chunk range from the nearest chunk spans and 32-char context", () => {
    const doc = makeDoc(PAPER);
    const [piece] = selectionToPieces(doc, rangeOver(doc, "ahead of time", "drops by 89%"), CHUNKS);
    expect(piece).toMatchObject({
      quote: "ahead of time. The latency drops by 89%",
      chunk_start: 102,
      chunk_end: 103,
    });
    expect(piece!.prefix.length).toBeLessThanOrEqual(32);
    expect(piece!.prefix.endsWith("the agent pose ")).toBe(true);
    expect(piece!.suffix.startsWith(" on all trajectories.")).toBe(true);
    expect(piece!.suffix.length).toBeLessThanOrEqual(32);
  });

  it("leaves chunk ids empty without a chunk map, and skips a translation", () => {
    const doc = makeDoc(PAPER);
    const [piece] = selectionToPieces(doc, rangeOver(doc, "We predict", "pose"), null);
    expect(piece).toMatchObject({ chunk_start: null, chunk_end: null });
    expect(selectionToPieces(doc, rangeOver(doc, "翻", "譯"), CHUNKS)).toEqual([]);
  });

  it("groups pieces by the section of their first chunk", () => {
    const doc = makeDoc(PAPER);
    const pieces = selectionToPieces(doc, rangeOver(doc, "which motivates", "agent pose"), CHUNKS);
    const groups = groupBySection(pieces, CHUNKS);
    expect(groups.map((g) => [g.section, g.pieces.length])).toEqual([
      ["1 Introduction", 1],
      ["2 Method", 1],
    ]);
  });
});

describe("findPiece", () => {
  it("re-finds the range a piece was made from (anchor round trip)", () => {
    const doc = makeDoc(PAPER);
    const original = rangeOver(doc, "latency drops", "trajectories.");
    const [piece] = selectionToPieces(doc, original, CHUNKS);
    const index = buildTextIndex(doc);
    const found = findPiece(index, piece!, CHUNKS);
    expect(found).not.toBeNull();
    expect(normaliseText(found!.toString())).toBe(piece!.quote);
    // The quote repeats in the next paragraph: the anchor picks its own.
    expect(found!.startContainer).toBe(original.startContainer);
    expect(found!.startOffset).toBe(original.startOffset);
  });

  it("picks the repeat whose chunk sits nearest when context is equal", () => {
    const doc = makeDoc(PAPER);
    const index = buildTextIndex(doc);
    const piece = {
      quote: "The latency drops by 89% on all trajectories.",
      chunk_start: 103,
      chunk_end: 103,
      prefix: "",
      suffix: "",
    };
    const found = findPiece(index, piece, CHUNKS)!;
    expect(found.startContainer.parentElement?.querySelector("#phchunk-2")).not.toBeNull();
  });

  it("returns null for a quote that is gone, and survives whitespace changes", () => {
    const doc = makeDoc(PAPER);
    const index = buildTextIndex(doc);
    expect(
      findPiece(index, { quote: "not in the paper", chunk_start: 101, chunk_end: 101, prefix: "", suffix: "" }, CHUNKS),
    ).toBeNull();
    const found = findPiece(
      index,
      { quote: "They often   fail when the scene", chunk_start: 101, chunk_end: 101, prefix: "", suffix: "" },
      CHUNKS,
    );
    expect(found && normaliseText(found.toString())).toBe("They often fail when the scene");
  });
});

describe("the reading text (shared with the backend's re-anchor)", () => {
  // src/lib/__fixtures__/mark_text: the backend's notes/html_text.py reads
  // the same file to the same text, or a quote is not found again.
  it("reads the shared fixture exactly as the backend does", () => {
    const html = ruleHtml;
    const want = JSON.parse(ruleExpected) as {
      text: string;
      spans: [string, number][];
    };
    const doc = new DOMParser().parseFromString(html, "text/html");
    const index = buildTextIndex(doc);
    expect(index.text).toBe(want.text);
    expect(index.spans.map((s) => [s.domId, s.at])).toEqual(want.spans);
  });

  it("keeps inline math in a quote, as its TeX, and finds it again", () => {
    const doc = makeDoc(
      `<p><span id="phchunk-0"></span>They generate hidden states <span class="math inline">\\(h_t\\)</span>,` +
        ` as a function of <span class="math inline">\\(h_{t-1}\\)</span> and the input.</p>`,
    );
    const chunks = chunkMap([{ id: 9, dom_id: "phchunk-0", section: "2 Background" }]);
    const range = rangeOver(doc, "hidden states", "function of");
    const [piece] = selectionToPieces(doc, range, chunks);
    expect(piece!.quote).toBe("hidden states $h_t$, as a function of");
    const found = findPiece(buildTextIndex(doc), piece!, chunks)!;
    expect(found.toString()).toContain("\\(h_t\\)");
    // A selection that starts inside the math takes it whole.
    const math = doc.querySelectorAll(".math")[1]!.firstChild as Text;
    const inner = doc.createRange();
    inner.setStart(math, 3);
    inner.setEnd(math.parentElement!.nextSibling!, 4);
    expect(selectionToPieces(doc, inner, chunks)[0]!.quote).toBe("$h_{t-1}$ and");
  });

  it("reads typeset math by its TeX annotation", () => {
    const doc = makeDoc(
      `<p>x <span class="math inline"><mjx-container><math><semantics><mi>h</mi>` +
        `<annotation encoding="application/x-tex">h_t</annotation></semantics></math></mjx-container></span> y</p>`,
    );
    expect(buildTextIndex(doc).text).toBe("x $h_t$ y");
  });

  it("files a heading selected alone under its own section", () => {
    const doc = makeDoc(PAPER);
    const [piece] = selectionToPieces(doc, rangeOver(doc, "2 Method", "2 Method"), CHUNKS);
    expect(piece).toMatchObject({ quote: "2 Method", chunk_start: 102, chunk_end: 102 });
    expect(groupBySection([piece!], CHUNKS)[0]!.section).toBe("2 Method");
  });
});
