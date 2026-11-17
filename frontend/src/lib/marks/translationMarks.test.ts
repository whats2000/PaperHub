import { act, createElement } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { useHtmlMarks } from "@/components/canvas/useHtmlMarks";
import { chunkMap, type ChunkMap } from "@/lib/marks/anchor";
import { attachHtmlMarks, type HtmlMarksOptions } from "@/lib/marks/htmlMarks";
import { joinTarget, translationPlace } from "@/lib/marks/pieces";
import {
  cardSelection,
  paragraphText,
  translationSelection,
} from "@/lib/marks/translationMarks";
import { useMarksStore } from "@/store/marks";
import { useNotesStore } from "@/store/notes";
import { REVEAL_TTL_MS, REVEAL_WAIT_MS, useTranslationStore } from "@/store/translation";
import type { Mark, MarkColorInfo, MarkCreated, MarkIn } from "@/types/notes";

const { toastFn, toastError, toastMessage, toastDismiss } = vi.hoisted(() => ({
  toastFn: vi.fn(),
  toastError: vi.fn(),
  toastMessage: vi.fn(),
  toastDismiss: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: toastError, message: toastMessage, success: vi.fn(), dismiss: toastDismiss }),
}));

const LANG = "繁體中文";
const SOURCE = "We predict the agent pose ahead of time.";
const TRANSLATED = "我們提前預測代理的姿態。";

/** A paper whose second paragraph has its translation under it (as the H1
 * overlay renders it), unless `translated` is false. */
function paper(translated = true, lang = LANG): string {
  const box = translated
    ? `<div class="ph-tr" data-ph-tr-key="h:3" data-ph-tr-state="ok" data-ph-tr-lang="${lang}" data-open=""><div class="ph-tr-inner">${TRANSLATED}</div></div>`
    : "";
  return `
  <h2>2 Method</h2>
  <p id="p1"><span id="phchunk-1"></span>Robots learn from demonstrations.</p>
  <p id="p2"><span id="phchunk-2"></span>${SOURCE}</p>${box}
  `;
}

const CHUNKS: ChunkMap = chunkMap([
  { id: 101, dom_id: "phchunk-1", section: "2 Method" },
  { id: 102, dom_id: "phchunk-2", section: "2 Method" },
]);

const COLORS: MarkColorInfo[] = [
  { color: "green", name: "Key result", visible: true },
  { color: "blue", name: "Method", visible: true },
  { color: "pink", name: "Question", visible: true },
  { color: "purple", name: "Disagree", visible: true },
];

function makeDoc(body: string): Document {
  const doc = document.implementation.createHTMLDocument("paper");
  doc.body.innerHTML = body;
  return doc;
}

function rangeOver(doc: Document, root: Element, text: string): Range {
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const node = n as Text;
    const at = node.data.indexOf(text);
    if (at < 0) continue;
    const r = doc.createRange();
    r.setStart(node, at);
    r.setEnd(node, at + text.length);
    return r;
  }
  throw new Error(`no ${text}`);
}

/** A mark made on the translation of the second paragraph. */
function trMark(over: Partial<Mark> = {}): Mark {
  return {
    id: "mt",
    paper_content_id: 3,
    block_id: "b_mt",
    kind: "highlight",
    color: "purple",
    view: "html",
    pieces: [
      {
        quote: "預測代理的姿態",
        orig: SOURCE,
        lang: LANG,
        source_ref: "h:3",
        chunk_start: 102,
        chunk_end: 102,
        prefix: "",
        suffix: "",
        tr_start: 4,
        tr_end: 11,
        tr_prefix: "我們提前",
        tr_suffix: "。",
      },
    ],
    lang: LANG,
    source_ref: "h:3",
    section_key: "2 Method",
    status: "ok",
    created_at: "2026-11-17T00:00:00Z",
    deleted_at: null,
    ...over,
  };
}

describe("translationSelection", () => {
  it("records the language, the translation block and the paragraph it translates", () => {
    const doc = makeDoc(paper());
    const box = doc.querySelector(".ph-tr")!;
    const placed = translationSelection(doc, rangeOver(doc, box, "預測代理的姿態"), CHUNKS);
    expect(placed).toHaveLength(1);
    const { piece, block, section } = placed[0]!;
    expect(piece).toMatchObject({
      quote: "預測代理的姿態",
      lang: LANG,
      source_ref: "h:3",
      orig: SOURCE,
      // The source paragraph's own anchor: filed and re-anchored by it.
      chunk_start: 102,
      chunk_end: 102,
      tr_start: 4,
      tr_end: 11,
      tr_prefix: "我們提前",
      tr_suffix: "。",
    });
    expect(block).toBe(doc.getElementById("p2"));
    expect(section).toBe("2 Method");
  });

  it("is empty for a selection on the paper's own text", () => {
    const doc = makeDoc(paper());
    expect(translationSelection(doc, rangeOver(doc, doc.body, "agent pose"), CHUNKS)).toEqual([]);
  });

  it("never joins a translation stroke with a mark on the original text", () => {
    const place = translationPlace(LANG, "h:1");
    expect(place).not.toBe("h:1");
    expect(joinTarget([{ id: "m", color: "green", places: ["h:1"] }], { color: "green", place })).toBeNull();
    expect(joinTarget([{ id: "m", color: "green", places: [place!] }], { color: "green", place })).toBe("m");
    // Another language is another place.
    expect(translationPlace("日本語", "h:1")).not.toBe(place);
  });
});

describe("PDF card pieces", () => {
  it("reads a paragraph's own text with its formulas as TeX", () => {
    expect(paragraphText("The loss ⟦0⟧ falls⟦BR⟧ over time.", ["L_t"])).toBe("The loss $L_t$ falls over time.");
  });

  it("makes a piece for the card's paragraph from a selection in the card", () => {
    const content = document.createElement("div");
    content.innerHTML = "<div>損失隨時間下降。</div>";
    document.body.appendChild(content);
    const range = rangeOver(document, content, "隨時間");
    const piece = cardSelection(
      content,
      range,
      { key: "p:/page/2/Text/4", text: "The loss falls over time.", math: [], page: 2 },
      LANG,
      [0.1, 0.2, 0.9, 0.3],
    );
    expect(piece).toMatchObject({
      quote: "隨時間",
      lang: LANG,
      source_ref: "p:/page/2/Text/4",
      orig: "The loss falls over time.",
      page: 2,
      block_id: "/page/2/Text/4",
      box: [0.1, 0.2, 0.9, 0.3],
      tr_start: 2,
    });
    content.remove();
  });
});

describe("drawing a mark on a translation", () => {
  let rects: MockInstance<(this: Range) => DOMRectList>;
  beforeEach(() => {
    rects = vi.spyOn(Range.prototype, "getClientRects").mockImplementation(function (this: Range) {
      const top = this.startContainer.parentElement?.closest(".ph-tr") ? 80 : 40;
      return [new DOMRect(this.startOffset * 6, top, 40, 16)] as unknown as DOMRectList;
    });
  });
  afterEach(() => rects.mockRestore());

  function opts(over: Partial<HtmlMarksOptions> = {}): HtmlMarksOptions {
    return { marks: [trMark()], colors: COLORS, chunks: CHUNKS, visible: true, focusIds: null, dark: false, ...over };
  }

  it("highlights the words in the translation while its language is on screen", () => {
    const doc = makeDoc(paper());
    const h = attachHtmlMarks(doc, opts({ translation: LANG }));
    expect(doc.querySelectorAll('.ph-mk[data-mark="mt"]')).toHaveLength(1);
    expect(doc.querySelector(".ph-mk-tr")).toBeNull();
    expect(h.shownAs("mt")).toBe("text");
    h.detach();
  });

  it("shows a 文 marker (no highlight) with translation off, another language, or the words changed", () => {
    for (const [doc, o] of [
      [makeDoc(paper(false)), opts({ translation: null })],
      [makeDoc(paper(true, "日本語")), opts({ translation: "日本語" })],
      [makeDoc(paper()), opts({ translation: LANG, marks: [trMark({ status: "translation_changed" })] })],
      [makeDoc(paper().replace(TRANSLATED, "全新的翻譯。")), opts({ translation: LANG })],
    ] as const) {
      const h = attachHtmlMarks(doc, o);
      expect(doc.querySelectorAll('.ph-mk[data-mark="mt"]')).toHaveLength(0);
      const marker = doc.querySelector<HTMLButtonElement>('.ph-mk-tr[data-mark="mt"]')!;
      expect(marker.textContent).toBe("文");
      expect(marker.dataset.color).toBe("purple");
      expect(h.shownAs("mt")).toBe("marker");
      h.detach();
    }
  });

  it("the marker is named, keyboard-reachable and hit-tested; both open the mark", () => {
    const doc = makeDoc(paper(false));
    const onMarker = vi.fn();
    const h = attachHtmlMarks(
      doc,
      opts({ translation: null, onMarker, markerLabel: (m) => `A mark on the ${m.lang} translation` }),
    );
    const marker = doc.querySelector<HTMLButtonElement>(".ph-mk-tr")!;
    expect(marker.tagName).toBe("BUTTON");
    expect(marker.tabIndex).toBe(0);
    expect(marker.getAttribute("aria-label")).toBe(`A mark on the ${LANG} translation`);
    // Enter / Space on the focused button.
    marker.click();
    expect(onMarker).toHaveBeenCalledWith("mt");
    // A pointer click lands on the page under the (pointer-events: none)
    // marker: hit-tested.
    const left = Number.parseFloat(marker.style.left);
    const top = Number.parseFloat(marker.style.top);
    doc.body.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: left + 4, clientY: top + 4 }));
    expect(onMarker).toHaveBeenCalledTimes(2);
    // "Show my marks" off: out of the tab order.
    h.update({ visible: false });
    expect(marker.tabIndex).toBe(-1);
    h.detach();
    expect(doc.querySelector(".ph-mk-trl")).toBeNull();
  });
});

describe("showMark", () => {
  afterEach(() => {
    vi.useRealTimers();
  });
  beforeEach(() => {
    toastFn.mockReset();
    toastDismiss.mockReset();
    useMarksStore.setState({ marksByPaper: { 3: [trMark()] } });
    useTranslationStore.setState({ language: "English", recent: [], enabledByPaper: {}, reveal: null });
  });

  it("turns translation on in the mark's language, asks the reader to show it, and offers Turn off", () => {
    const prev = useTranslationStore.getState().showMark("mt");
    expect(prev).toEqual({ paperId: 3, enabled: false, language: "English" });
    const s = useTranslationStore.getState();
    expect(s.enabledByPaper[3]).toBe(true);
    expect(s.language).toBe(LANG);
    expect(s.reveal).toMatchObject({ paperId: 3, markId: "mt" });
    expect(toastFn).toHaveBeenCalledWith(
      "Translation turned on (繁中) to show this mark",
      expect.anything(),
    );
    const { action } = toastFn.mock.calls[0]![1] as { action: { label: string; onClick: () => void } };
    expect(action.label).toBe("Turn off");
    action.onClick();
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(false);
    expect(useTranslationStore.getState().language).toBe("English");
  });

  it("switches from another language with Back to <language>", () => {
    useTranslationStore.setState({ language: "日本語", enabledByPaper: { 3: true } });
    useTranslationStore.getState().showMark("mt");
    expect(useTranslationStore.getState().language).toBe(LANG);
    const opts = toastFn.mock.calls[0]![1] as { action: { label: string; onClick: () => void } };
    expect(opts.action.label).toBe("Back to 日本語");
    opts.action.onClick();
    expect(useTranslationStore.getState().language).toBe("日本語");
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(true);
  });

  it("changes nothing (and says nothing) when the mark's language is already on", () => {
    useTranslationStore.setState({ language: LANG, enabledByPaper: { 3: true } });
    const before = useTranslationStore.getState().reveal;
    expect(useTranslationStore.getState().showMark("mt")).toEqual({ paperId: 3, enabled: true, language: LANG });
    expect(toastFn).not.toHaveBeenCalled();
    expect(useTranslationStore.getState().reveal).not.toBe(before);
  });

  it("restores nothing once the user has changed the translation since", () => {
    useTranslationStore.getState().showMark("mt");
    const { action } = toastFn.mock.calls[0]![1] as { action: { onClick: () => void } };
    // The user picks another language in the pill...
    useTranslationStore.getState().setLanguage("日本語");
    expect(toastDismiss).toHaveBeenCalledWith("tr-show-mark");
    action.onClick();
    // ...so Turn off is stale: their choice stays.
    expect(useTranslationStore.getState().language).toBe("日本語");
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(true);
  });

  it("restores nothing after the user turned translation off and on again elsewhere", () => {
    useTranslationStore.getState().showMark("mt");
    const { action } = toastFn.mock.calls[0]![1] as { action: { onClick: () => void } };
    useTranslationStore.getState().toggle(3);
    action.onClick();
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(false);
    expect(useTranslationStore.getState().language).toBe(LANG);
  });

  it("two in a row leave one toast whose way back is the state before the first", () => {
    useMarksStore.setState({ marksByPaper: { 3: [trMark(), trMark({ id: "mj", lang: "日本語" })] } });
    useTranslationStore.getState().showMark("mt");
    useTranslationStore.getState().showMark("mj");
    expect(toastFn).toHaveBeenCalledTimes(2);
    const ids = toastFn.mock.calls.map((c) => (c[1] as { id?: string }).id);
    expect(ids).toEqual(["tr-show-mark", "tr-show-mark"]);
    const { action } = toastFn.mock.calls[1]![1] as { action: { label: string; onClick: () => void } };
    expect(action.label).toBe("Turn off");
    action.onClick();
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(false);
    expect(useTranslationStore.getState().language).toBe("English");
  });

  it("adds the mark's language to the recent languages, like a pick in the pill", () => {
    useTranslationStore.getState().showMark("mt");
    expect(useTranslationStore.getState().recent[0]).toBe(LANG);
  });

  it("a request no reader takes up is dropped (a paper that never opens)", () => {
    vi.useFakeTimers();
    useTranslationStore.getState().showMark("mt");
    vi.advanceTimersByTime(REVEAL_TTL_MS + 10);
    expect(useTranslationStore.getState().reveal).toBeNull();
    // One a reader took up stays until that reader is done.
    useTranslationStore.getState().showMark("mt");
    useTranslationStore.getState().claimReveal(useTranslationStore.getState().reveal!.n);
    vi.advanceTimersByTime(REVEAL_TTL_MS + 10);
    expect(useTranslationStore.getState().reveal).not.toBeNull();
  });

  it("is null for a mark on the paper's own text", () => {
    useMarksStore.setState({ marksByPaper: { 3: [trMark({ id: "mo", lang: null, source_ref: null })] } });
    expect(useTranslationStore.getState().showMark("mo")).toBeNull();
    expect(toastFn).not.toHaveBeenCalled();
  });
});

describe("useHtmlMarks on a translation", () => {
  function frameDoc(body: string): Document {
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    const doc = frame.contentDocument!;
    doc.body.innerHTML = body;
    const FrameRange = (frame.contentWindow as unknown as { Range: typeof Range }).Range;
    FrameRange.prototype.getClientRects = () => [new DOMRect(10, 40, 30, 16)] as unknown as DOMRectList;
    FrameRange.prototype.getBoundingClientRect = () => new DOMRect(10, 40, 100, 16);
    return doc;
  }
  function Host({ doc }: { doc: Document }) {
    return createElement("div", null, useHtmlMarks({ paperId: 3, doc, isDark: false }));
  }
  function selectIn(doc: Document, text: string) {
    const sel = doc.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(rangeOver(doc, doc.querySelector(".ph-tr")!, text));
    act(() => {
      doc.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
  }
  function created(body: MarkIn): MarkCreated {
    return {
      mark: { ...trMark({ id: `mk_${body.pieces.length}` }), pieces: body.pieces },
      block: null,
      saved_to: { note_id: 7, paper_title: "Duo", section: body.section_key, part_index: 0, part_count: 1 },
    };
  }

  let create: ReturnType<typeof vi.fn<(body: MarkIn, opts?: object) => Promise<MarkCreated>>>;
  beforeEach(() => {
    toastFn.mockReset();
    create = vi.fn((body: MarkIn) => Promise.resolve(created(body)));
    useMarksStore.setState({
      marksByPaper: {},
      chunksByPaper: {},
      colors: COLORS,
      showMarks: true,
      penOn: false,
      penSession: [],
      loadPaper: vi.fn(() => Promise.resolve()),
      loadColors: vi.fn(() => Promise.resolve()),
      ensureChunks: vi.fn(() => Promise.resolve(CHUNKS)),
      create,
    });
    useNotesStore.setState({ caretAfter: null, byTarget: {}, quoteTarget: null });
    useTranslationStore.setState({ language: LANG, enabledByPaper: { 3: true }, reveal: null });
  });
  afterEach(() => {
    document.querySelectorAll("iframe").forEach((f) => f.remove());
  });

  it("a selection in the translation opens the popover and saves a translation mark", async () => {
    const doc = frameDoc(paper());
    render(createElement(Host, { doc }));
    selectIn(doc, "預測代理的姿態");
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Disagree" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "highlight",
      color: "purple",
      view: "html",
      lang: LANG,
      source_ref: "h:3",
      section_key: "2 Method",
      pieces: [{ quote: "預測代理的姿態", lang: LANG, source_ref: "h:3", orig: SOURCE, chunk_start: 102 }],
    });
  });

  it("a selection from a paragraph into its translation marks both, the original and the translated words", async () => {
    const doc = frameDoc(paper());
    render(createElement(Host, { doc }));
    const sel = doc.getSelection()!;
    sel.removeAllRanges();
    const range = doc.createRange();
    range.setStart(rangeOver(doc, doc.getElementById("p2")!, "ahead of time.").startContainer, SOURCE.indexOf("ahead"));
    const tr = rangeOver(doc, doc.querySelector(".ph-tr")!, "我們提前");
    range.setEnd(tr.endContainer, tr.endOffset);
    sel.addRange(range);
    act(() => {
      doc.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Disagree" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    const bodies = create.mock.calls.map((c) => c[0]);
    expect(bodies[0]).toMatchObject({ pieces: [{ quote: "ahead of time." }] });
    expect(bodies[0]!.lang ?? null).toBeNull();
    expect(bodies[1]).toMatchObject({ lang: LANG, source_ref: "h:3", pieces: [{ quote: "我們提前" }] });
  });

  it("a drag in a translation still loading says there is nothing to mark", () => {
    const doc = frameDoc(paper().replace('data-ph-tr-state="ok"', 'data-ph-tr-state="pending"'));
    render(createElement(Host, { doc }));
    vi.useFakeTimers();
    selectIn(doc, "預測代理");
    act(() => {
      vi.runAllTimers();
    });
    vi.useRealTimers();
    expect(toastMessage).toHaveBeenCalledWith("There are no words in this selection to mark.");
    expect(screen.queryByRole("toolbar", { name: "Mark the selection" })).toBeNull();
  });

  it("a highlighter stroke in the translation makes a translation piece at once", async () => {
    useMarksStore.setState({ penOn: true, penColor: "blue" });
    const doc = frameDoc(paper());
    render(createElement(Host, { doc }));
    selectIn(doc, "提前預測");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      color: "blue",
      lang: LANG,
      source_ref: "h:3",
      pieces: [{ quote: "提前預測", source_ref: "h:3", orig: SOURCE }],
    });
    // The pen session records the translation place, not the paragraph's.
    const session = useMarksStore.getState().penSession;
    expect(session[0]!.places[0]).toMatch(/^t:繁體中文:h:/);
  });

  it("a request to show a mark flashes its words in the translation, then is done", async () => {
    useMarksStore.setState({ marksByPaper: { 3: [trMark()] }, chunksByPaper: { 3: CHUNKS } });
    const doc = frameDoc(paper());
    render(createElement(Host, { doc }));
    act(() => {
      useTranslationStore.setState({ reveal: { paperId: 3, markId: "mt", n: 5 } });
    });
    await waitFor(() => expect(useTranslationStore.getState().reveal).toBeNull());
    expect(doc.querySelector('.ph-mk[data-mark="mt"]')!.hasAttribute("data-flash")).toBe(true);
  });

  it("a click on the 文 marker turns translation on to show the mark", () => {
    useTranslationStore.setState({ language: "English", enabledByPaper: {} });
    useMarksStore.setState({ marksByPaper: { 3: [trMark()] }, chunksByPaper: { 3: CHUNKS } });
    const doc = frameDoc(paper(false));
    render(createElement(Host, { doc }));
    const marker = doc.querySelector<HTMLButtonElement>('.ph-mk-tr[data-mark="mt"]')!;
    expect(marker.getAttribute("aria-label")).toBe("A mark on the 繁中 translation. Show it");
    const showMark = vi.spyOn(useTranslationStore.getState(), "showMark");
    act(() => marker.click());
    expect(showMark).toHaveBeenCalledTimes(1);
    showMark.mockRestore();
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(true);
    expect(useTranslationStore.getState().language).toBe(LANG);
    expect(toastFn).toHaveBeenCalledWith("Translation turned on (繁中) to show this mark", expect.anything());
  });
});

describe("useHtmlMarks on a translation: what the reader does next", () => {
  function frameDoc(body: string): Document {
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    const doc = frame.contentDocument!;
    doc.body.innerHTML = body;
    const FrameRange = (frame.contentWindow as unknown as { Range: typeof Range }).Range;
    FrameRange.prototype.getClientRects = () => [new DOMRect(10, 40, 30, 16)] as unknown as DOMRectList;
    FrameRange.prototype.getBoundingClientRect = () => new DOMRect(10, 40, 100, 16);
    return doc;
  }
  function Host({ doc }: { doc: Document }) {
    return createElement("div", null, useHtmlMarks({ paperId: 3, doc, isDark: false }));
  }
  const nextFrame = (doc: Document) =>
    act(() => new Promise<void>((r) => doc.defaultView!.requestAnimationFrame(() => setTimeout(r, 0))));
  /** The H1 overlay putting the translation under the second paragraph. */
  function addBox(doc: Document, state = "ok", text = TRANSLATED, lang = LANG) {
    const box = doc.createElement("div");
    box.className = "ph-tr";
    box.setAttribute("data-ph-tr-key", "h:3");
    box.setAttribute("data-ph-tr-state", state);
    box.setAttribute("data-ph-tr-lang", lang);
    box.innerHTML = `<div class="ph-tr-inner">${text}</div>`;
    doc.getElementById("p2")!.after(box);
    return box;
  }

  beforeEach(() => {
    toastFn.mockReset();
    useMarksStore.setState({
      marksByPaper: { 3: [trMark()] },
      chunksByPaper: { 3: CHUNKS },
      colors: COLORS,
      showMarks: true,
      penOn: false,
      loadPaper: vi.fn(() => Promise.resolve()),
      loadColors: vi.fn(() => Promise.resolve()),
    });
    useTranslationStore.setState({ language: LANG, enabledByPaper: { 3: true }, reveal: null });
  });
  afterEach(() => {
    vi.useRealTimers();
    document.querySelectorAll("iframe").forEach((f) => f.remove());
  });

  it("swaps the 文 marker for the highlight as the translation arrives, and back as it leaves", async () => {
    const doc = frameDoc(paper(false));
    render(createElement(Host, { doc }));
    expect(doc.querySelector('.ph-mk-tr[data-mark="mt"]')).not.toBeNull();
    const box = addBox(doc);
    await nextFrame(doc);
    await waitFor(() => expect(doc.querySelector('.ph-mk[data-mark="mt"]')).not.toBeNull());
    expect(doc.querySelector('.ph-mk-tr[data-mark="mt"]:not([data-leaving])')).toBeNull();
    // Translation turned off: the overlay collapses the box away.
    box.setAttribute("data-ph-tr-leaving", "");
    await nextFrame(doc);
    await waitFor(() => expect(doc.querySelector('.ph-mk-tr[data-mark="mt"]:not([data-leaving])')).not.toBeNull());
    expect(doc.querySelector('.ph-mk[data-mark="mt"]:not([data-leaving])')).toBeNull();
  });

  it("the 文 marker turns translation on; once the translation arrives its words flash", async () => {
    useTranslationStore.setState({ language: "English", enabledByPaper: {} });
    const doc = frameDoc(paper(false));
    render(createElement(Host, { doc }));
    act(() => doc.querySelector<HTMLButtonElement>('.ph-mk-tr[data-mark="mt"]')!.click());
    expect(useTranslationStore.getState().reveal).toMatchObject({ markId: "mt" });
    // The overlay fills the paragraph's translation a moment later.
    addBox(doc);
    await nextFrame(doc);
    await waitFor(() => expect(useTranslationStore.getState().reveal).toBeNull());
    expect(doc.querySelector('.ph-mk[data-mark="mt"]')!.hasAttribute("data-flash")).toBe(true);
  });

  it.each([
    ["the paragraph's translation failed", () => paper(false), "unavailable", "This paragraph's translation isn't available, so the mark can't be shown in it"],
    ["the words left the translation", () => paper(false), "changed-text", "The translation changed since you marked it"],
  ])("says why when %s, with Show original", async (_why, body, kind, message) => {
    const doc = frameDoc(body());
    if (kind === "unavailable") addBox(doc, "unavailable", "");
    else addBox(doc, "ok", "全新的翻譯。");
    render(createElement(Host, { doc }));
    act(() => {
      useTranslationStore.getState().showMark("mt");
    });
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith(message, expect.anything()));
    const { action } = toastFn.mock.calls.find((c) => c[0] === message)![1] as { action: { label: string; onClick: () => void } };
    expect(action.label).toBe("Show original");
    action.onClick();
    expect(toastFn).toHaveBeenLastCalledWith("Original text", expect.objectContaining({ description: SOURCE }));
    expect(useTranslationStore.getState().reveal).toBeNull();
  });

  it("says the translation changed for a mark the re-anchor flagged", async () => {
    useMarksStore.setState({ marksByPaper: { 3: [trMark({ status: "translation_changed" })] } });
    const doc = frameDoc(paper());
    render(createElement(Host, { doc }));
    act(() => {
      useTranslationStore.getState().showMark("mt");
    });
    await waitFor(() =>
      expect(toastFn).toHaveBeenCalledWith("The translation changed since you marked it", expect.anything()),
    );
  });

  it("waits for a slow translation, then says it didn't arrive", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const doc = frameDoc(paper(false));
    addBox(doc, "pending", "");
    render(createElement(Host, { doc }));
    act(() => {
      useTranslationStore.getState().showMark("mt");
    });
    act(() => {
      vi.advanceTimersByTime(REVEAL_WAIT_MS / 2);
    });
    expect(useTranslationStore.getState().reveal).not.toBeNull();
    expect(toastFn).not.toHaveBeenCalledWith("The translation didn't arrive in time to show this mark", expect.anything());
    act(() => {
      vi.advanceTimersByTime(REVEAL_WAIT_MS);
    });
    expect(toastFn).toHaveBeenCalledWith("The translation didn't arrive in time to show this mark", expect.anything());
    expect(useTranslationStore.getState().reveal).toBeNull();
  });
});
