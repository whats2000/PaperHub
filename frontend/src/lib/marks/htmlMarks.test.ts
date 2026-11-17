import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { buildTextIndex, chunkMap } from "@/lib/marks/anchor";
import {
  MK_STYLE_ID,
  attachHtmlMarks,
  darkPreimage,
  darkRendered,
  markableObjects,
  type HtmlMarksOptions,
} from "@/lib/marks/htmlMarks";
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
let rangeRects: MockInstance<(this: Range) => DOMRectList>;
beforeEach(() => {
  rangeRects = vi.spyOn(Range.prototype, "getClientRects").mockImplementation(function (this: Range) {
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
    // Quoted passages show as boxes only: margin icons are for notes with no quote.
    expect(doc.querySelectorAll(".ph-mk-icon")).toHaveLength(0);
  });

  it("puts a note with no quote in the margin, one icon with a count per line", () => {
    const doc = makeDoc(PAPER);
    const handle = attachHtmlMarks(
      doc,
      opts({
        chunks: null,
        marks: [mark("m1", "", 102, { kind: "quote", color: null }), mark("m3", "", 102, { kind: "quote", color: null })],
      }),
    );
    // Its place is its chunk's span: none until the chunk map is there.
    expect(doc.querySelectorAll(".ph-mk-icon")).toHaveLength(0);
    handle.update({ chunks: CHUNKS });
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
    expect(onClick).toHaveBeenCalledWith("m1", 0);
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

  it("draws each piece of a joined mark, the skipped words unmarked, and reports the piece clicked", () => {
    const doc = makeDoc(PAPER);
    const onClick = vi.fn();
    const two = mark("m5", "We predict", 102, {
      pieces: [
        { quote: "We predict", chunk_start: 102, chunk_end: 102, prefix: "", suffix: "" },
        { quote: "gone", chunk_start: 102, chunk_end: 102, prefix: "", suffix: "", erased: true },
        { quote: "ahead of time", chunk_start: 102, chunk_end: 102, prefix: "", suffix: "" },
      ],
    });
    attachHtmlMarks(doc, opts({ marks: [two], onClick }));
    const boxes = rects(doc, "m5");
    expect(boxes).toHaveLength(2);
    // "the agent pose" between the pieces is not covered.
    expect(boxes[0]!.style.width).toBe(`${"We predict".length * 6}px`);
    expect(boxes[1]!.style.left).toBe(`${"We predict the agent pose ".length * 6}px`);
    const second = "We predict the agent pose ".length * 6 + 3;
    doc.body.dispatchEvent(new MouseEvent("click", { clientX: second, clientY: 25, bubbles: true }));
    expect(onClick).toHaveBeenLastCalledWith("m5", 2);
    doc.body.dispatchEvent(new MouseEvent("click", { clientX: 3, clientY: 25, bubbles: true }));
    expect(onClick).toHaveBeenLastCalledWith("m5", 0);
  });

  it("with the highlighter on, a selection shows in the pen colour; off again, the page is as it was", () => {
    const doc = makeDoc(PAPER);
    const before = doc.documentElement.outerHTML;
    const handle = attachHtmlMarks(doc, opts({ pen: "blue" }));
    expect(doc.documentElement.getAttribute("data-ph-pen")).toBe("blue");
    expect(doc.getElementById(MK_STYLE_ID)!.textContent).toContain('html[data-ph-pen="blue"] ::selection');
    // The dashed "click to erase" ring fades rather than snapping on.
    expect(doc.getElementById(MK_STYLE_ID)!.textContent).toContain("outline-color 150ms ease-out");
    handle.update({ pen: null });
    expect(doc.documentElement.hasAttribute("data-ph-pen")).toBe(false);
    handle.update({ pen: "pink" });
    handle.detach();
    expect(doc.documentElement.outerHTML).toBe(before);
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

describe("attachHtmlMarks in a live document", () => {
  function frameDoc(body: string): Document {
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    const doc = frame.contentDocument!;
    doc.body.innerHTML = body;
    // The frame's own Range: give it the same fake geometry.
    const FrameRange = (frame.contentWindow as unknown as { Range: typeof Range }).Range;
    FrameRange.prototype.getClientRects = function (this: Range) {
      return Range.prototype.getClientRects.call(this);
    };
    return doc;
  }

  it("redraws when the paper reflows without a resize (math typeset, a translation)", async () => {
    const doc = frameDoc(PAPER);
    attachHtmlMarks(doc, opts());
    expect(rects(doc, "m1")[0]!.style.top).toBe("20px");
    rangeRects.mockImplementation(
      () => [new DOMRect(0, 44, 30, 16)] as unknown as DOMRectList,
    );
    doc.getElementById("p2")!.appendChild(doc.createTextNode(" More."));
    await vi.waitFor(() => expect(rects(doc, "m1")[0]!.style.top).toBe("44px"));
  });

  it("takes a click on a mark over a link: the link and the lightbox stay put", () => {
    const doc = frameDoc(
      '<p id="p1"><span id="phchunk-1"></span>See <a href="#fig1">the agent pose</a> here.</p>',
    );
    const onClick = vi.fn();
    const lightbox = vi.fn();
    doc.addEventListener("click", lightbox);
    attachHtmlMarks(doc, opts({ marks: [mark("m1", "the agent pose", 102)], onClick }));
    const a = doc.querySelector("a")!;
    const ev = new MouseEvent("click", { clientX: 3, clientY: 25, bubbles: true, cancelable: true });
    a.dispatchEvent(ev);
    expect(onClick).toHaveBeenCalledWith("m1", 0);
    expect(ev.defaultPrevented).toBe(true);
    expect(lightbox).not.toHaveBeenCalled();
  });
});

describe("the dark reader", () => {
  it("draws each colour so the inverted page shows the validated dark palette", () => {
    const channels = (hex: string) => [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16));
    const off = (a: string, b: string) => Math.max(...channels(a).map((v, i) => Math.abs(v - channels(b)[i]!)));
    for (const hex of ["#36a067", "#4b86dc", "#9670dc", "#8a8a8a", "#bdbdbd"]) {
      expect(off(darkRendered(darkPreimage(hex)), hex)).toBeLessThanOrEqual(1); // rounding
    }
    // Pink is at the gamut's edge: within a few steps per channel.
    expect(off(darkRendered(darkPreimage("#d9578a")), "#d9578a")).toBeLessThanOrEqual(4);
    const doc = makeDoc(PAPER);
    attachHtmlMarks(doc, opts({ dark: true }));
    const layer = doc.querySelector<HTMLElement>(".ph-mk-layer")!;
    expect(layer.style.getPropertyValue("--mk-green")).toBe(darkPreimage("#36a067"));
    expect(layer.style.getPropertyValue("--mk-alpha")).toBe("32%");
    expect(layer.style.filter).toBe("");
  });
});

describe("figures and display equations", () => {
  const OBJECTS = `
    <p id="p1"><span id="phchunk-1"></span>We predict the agent pose ahead of time.</p>
    <span id="phfig-001"></span><figure id="f1"><img alt="arm"><figcaption>Figure 1: Robot arm.</figcaption></figure>
    <p id="p2">Inline <span class="math inline">\\(x\\)</span> stays text; <span class="math display">\\[E = mc^2\\]</span></p>
  `;

  it("finds the figures behind phfig anchors and the display equations, not inline math", () => {
    const doc = makeDoc(OBJECTS);
    expect(markableObjects(doc).map((o) => [o.kind, o.figureId, o.el.className || o.el.id])).toEqual([
      ["figure", "fig-001", "f1"],
      ["equation", null, "math display"],
    ]);
  });

  it("draws a figure mark as one ring around the figure, in its colour", () => {
    const doc = makeDoc(OBJECTS);
    doc.getElementById("f1")!.getBoundingClientRect = () => new DOMRect(20, 100, 300, 200);
    const fig = mark("mf", "Figure 1: Robot arm.", 102, {
      kind: "figure",
      color: "pink",
      pieces: [{ quote: "Figure 1: Robot arm.", chunk_start: 102, chunk_end: 102, prefix: "", suffix: "", figure_id: "fig-001" }],
    });
    attachHtmlMarks(doc, opts({ marks: [fig] }));
    const boxes = doc.querySelectorAll<HTMLElement>('.ph-mk[data-mark="mf"]');
    expect(boxes).toHaveLength(1);
    expect(boxes[0]!.dataset.kind).toBe("figure");
    expect(boxes[0]!.dataset.color).toBe("pink");
    expect(boxes[0]!.style.width).toBe("300px");
    expect(doc.getElementById(MK_STYLE_ID)!.textContent).toContain('.ph-mk[data-kind="figure"]');
  });
});

describe("marks made on the PDF tab", () => {
  it("are drawn from their span in the reading text; without one, not at all", () => {
    const doc = makeDoc(PAPER);
    const text = buildTextIndex(doc).text;
    const at = text.indexOf("agent pose");
    const pdf = mark("mp", "agent pose", 102, {
      view: "pdf",
      color: "blue",
      pieces: [
        { quote: "agent pose", page: 0, rects: [[0.1, 0.1, 0.3, 0.12]], chunk_start: 102, chunk_end: 102, text_start: at, text_end: at + 10 },
      ],
    });
    const lost = mark("mq", "latency drops", 103, {
      view: "pdf",
      pieces: [{ quote: "latency drops", page: 0, rects: [[0.1, 0.2, 0.3, 0.22]] }], // never located
    });
    attachHtmlMarks(doc, opts({ marks: [pdf, lost] }));
    const boxes = doc.querySelectorAll<HTMLElement>('.ph-mk[data-mark="mp"]');
    expect(boxes).toHaveLength(1);
    expect(boxes[0]!.dataset.color).toBe("blue");
    // The range drawn is the stored span (left = offset in its text node).
    const node = doc.getElementById("p1")!.lastChild as Text;
    expect(boxes[0]!.style.left).toBe(`${node.data.indexOf("agent pose") * 6}px`);
    expect(doc.querySelector('.ph-mk[data-mark="mq"]')).toBeNull();
  });
});
