import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { chunkMap } from "@/lib/marks/anchor";
import { MK_STYLE_ID, attachHtmlMarks, type HtmlMarksOptions } from "@/lib/marks/htmlMarks";
import type { Mark, MarkColorInfo } from "@/types/notes";

function makeDoc(body: string): Document {
  const doc = document.implementation.createHTMLDocument("paper");
  doc.body.innerHTML = body;
  return doc;
}

const PAPER = `
  <h2>2 Method</h2>
  <p id="p1"><span id="phchunk-1"></span>We predict the agent pose ahead of time.</p>
  <p id="p2"><span id="phchunk-2"></span>The latency drops by 89% on all trajectories.</p>
`;

const CHUNKS = chunkMap([
  { id: 102, dom_id: "phchunk-1", section: "2 Method" },
  { id: 103, dom_id: "phchunk-2", section: "2 Method" },
]);

const COLORS: MarkColorInfo[] = [
  { color: "green", name: "Key result", visible: true },
  { color: "blue", name: "Method", visible: true },
  { color: "pink", name: "Question", visible: true },
  { color: "purple", name: "Disagree", visible: true },
];

function mark(id: string, quote: string, chunk: number, over: Partial<Mark> = {}): Mark {
  return {
    id,
    paper_content_id: 7,
    block_id: `b_${id}`,
    kind: "highlight",
    color: "green",
    view: "html",
    pieces: [{ quote, chunk_start: chunk, chunk_end: chunk, prefix: "", suffix: "" }],
    lang: null,
    source_ref: null,
    section_key: "2 Method",
    status: "ok",
    created_at: "2026-11-17T00:00:00Z",
    deleted_at: null,
    ...over,
  };
}

function opts(over: Partial<HtmlMarksOptions> = {}): HtmlMarksOptions {
  return {
    marks: [mark("m1", "agent pose", 102), mark("m2", "latency drops", 103, { kind: "quote", color: null })],
    colors: COLORS,
    chunks: CHUNKS,
    visible: true,
    focusIds: null,
    dark: false,
    ...over,
  };
}

// jsdom has no layout: every range gets one line box per text it covers,
// placed by the paragraph it sits in.
beforeEach(() => {
  vi.spyOn(Range.prototype, "getClientRects").mockImplementation(function (this: Range) {
    const p = this.startContainer.parentElement?.closest("p");
    const top = p?.id === "p2" ? 60 : 20;
    const left = this.startOffset * 6;
    const width = (this.endOffset - this.startOffset) * 6;
    return [new DOMRect(left, top, width, 16)] as unknown as DOMRectList;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const rects = (doc: Document, id?: string) =>
  Array.from(doc.querySelectorAll<HTMLElement>(id ? `.ph-mk[data-mark="${id}"]` : ".ph-mk"));

describe("attachHtmlMarks", () => {
  it("draws a highlight as a coloured box and a quote as an underline", () => {
    const doc = makeDoc(PAPER);
    attachHtmlMarks(doc, opts());
    expect(doc.getElementById(MK_STYLE_ID)).not.toBeNull();
    const [hl] = rects(doc, "m1");
    expect(hl).toBeDefined();
    expect(hl!.dataset.kind).toBe("highlight");
    expect(hl!.dataset.color).toBe("green");
    expect(hl!.style.top).toBe("20px");
    expect(hl!.style.width).toBe(`${"agent pose".length * 6}px`);
    const [q] = rects(doc, "m2");
    expect(q!.dataset.kind).toBe("quote");
    expect(q!.dataset.color).toBeUndefined();
    // A margin icon on each marked line.
    expect(doc.querySelectorAll(".ph-mk-icon")).toHaveLength(2);
  });

  it("counts the marks that share a line on one margin icon", () => {
    const doc = makeDoc(PAPER);
    attachHtmlMarks(
      doc,
      opts({ marks: [mark("m1", "agent pose", 102), mark("m3", "ahead of time", 102, { color: "blue" })] }),
    );
    const icons = doc.querySelectorAll<HTMLElement>(".ph-mk-icon");
    expect(icons).toHaveLength(1);
    expect(icons[0]!.textContent).toBe("2");
    expect(icons[0]!.dataset.marks).toBe("m1 m3");
  });

  it("recolours on update, drops a hidden colour and hides everything when off", () => {
    vi.useFakeTimers();
    const doc = makeDoc(PAPER);
    const handle = attachHtmlMarks(doc, opts());
    handle.update({ marks: [mark("m1", "agent pose", 102, { color: "pink" })] });
    expect(rects(doc, "m1")[0]!.dataset.color).toBe("pink");
    // The removed quote leaves (fades), then is gone.
    expect(rects(doc, "m2")[0]!.dataset.leaving).toBe("");
    vi.advanceTimersByTime(300);
    expect(rects(doc, "m2")).toHaveLength(0);

    handle.update({ colors: COLORS.map((c) => (c.color === "pink" ? { ...c, visible: false } : c)) });
    expect(rects(doc, "m1")[0]!.dataset.leaving).toBe("");
    vi.advanceTimersByTime(300);
    expect(rects(doc, "m1")).toHaveLength(0);

    handle.update({ colors: COLORS, visible: false });
    const layer = doc.querySelector<HTMLElement>(".ph-mk-layer")!;
    expect(layer.dataset.hidden).toBe("");
    // Hidden, never deleted: turning marks back on shows them again.
    expect(rects(doc, "m1")).toHaveLength(1);
    handle.update({ visible: true });
    expect(layer.dataset.hidden).toBeUndefined();
  });

  it("dims marks outside the focused note", () => {
    const doc = makeDoc(PAPER);
    attachHtmlMarks(doc, opts({ focusIds: new Set(["m2"]) }));
    expect(rects(doc, "m1")[0]!.dataset.dim).toBe("");
    expect(rects(doc, "m2")[0]!.dataset.dim).toBeUndefined();
  });

  it("does not draw a mark whose quote is gone", () => {
    const doc = makeDoc(PAPER);
    attachHtmlMarks(doc, opts({ marks: [mark("m9", "no longer in the paper", 102)] }));
    expect(rects(doc)).toHaveLength(0);
  });

  it("hit-tests clicks and hovers, so the text under a mark stays selectable", () => {
    const doc = makeDoc(PAPER);
    const onClick = vi.fn();
    const onHover = vi.fn();
    attachHtmlMarks(doc, opts({ onClick, onHover }));
    const layer = doc.querySelector<HTMLElement>(".ph-mk-layer")!;
    expect(getComputedStyle(layer).pointerEvents).toBe("none");
    const x = "We predict the ".length * 6 + 3; // inside "agent pose"
    doc.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: 25, bubbles: true }));
    expect(onHover).toHaveBeenLastCalledWith("m1");
    expect(rects(doc, "m1")[0]!.dataset.hot).toBe("");
    doc.body.dispatchEvent(new MouseEvent("click", { clientX: x, clientY: 25, bubbles: true }));
    expect(onClick).toHaveBeenCalledWith("m1");
    doc.dispatchEvent(new MouseEvent("mousemove", { clientX: 5, clientY: 300, bubbles: true }));
    expect(onHover).toHaveBeenLastCalledWith(null);
  });

  it("flashes a mark", () => {
    vi.useFakeTimers();
    const doc = makeDoc(PAPER);
    const handle = attachHtmlMarks(doc, opts());
    handle.flash("m1");
    expect(rects(doc, "m1")[0]!.dataset.flash).toBe("");
    vi.advanceTimersByTime(2000);
    expect(rects(doc, "m1")[0]!.dataset.flash).toBeUndefined();
  });

  it("leaves the document as it was once detached", () => {
    const doc = makeDoc(PAPER);
    const before = doc.documentElement.outerHTML;
    const handle = attachHtmlMarks(doc, opts());
    expect(doc.documentElement.outerHTML).not.toBe(before);
    handle.detach();
    expect(doc.documentElement.outerHTML).toBe(before);
  });
});
