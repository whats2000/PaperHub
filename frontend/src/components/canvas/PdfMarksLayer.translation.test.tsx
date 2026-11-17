import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useContext, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BLOCKS, layOut, pdfMark, place, resetStores, stubRangeGeometry, type CreateMock } from "@/components/canvas/__fixtures__/pdfMarks";
import { PdfMarksPage } from "@/components/canvas/PdfMarksLayer";
import { TranslationCard } from "@/components/canvas/TranslationCard";
import { PdfMarksContext, PdfMarksProvider, usePdfMarks } from "@/components/canvas/usePdfMarks";
import { listPaperBlocks } from "@/lib/api";
import type { CardPlacement } from "@/lib/cardPlacement";
import type { TranslationPiece } from "@/lib/marks/translationMarks";
import { useCanvasStore } from "@/store/canvas";
import { useMarkNavStore } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useTranslationStore } from "@/store/translation";

/** The notes pane open beside the reader (side by side). */
function paneBeside() {
  useNotesPaneStore.setState({ open: true, openAt: vi.fn() });
  useCanvasStore.setState({ open: true, width: 900 });
}
import type { Mark } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), listPaperBlocks: vi.fn() }));
const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: vi.fn(), message: vi.fn(), success: vi.fn() }),
}));

const LANG = "繁體中文";
const KEY = "p:/page/0/Text/1";

const PIECE: TranslationPiece = {
  quote: "從示範學習",
  orig: "Robots learn from demos.",
  lang: LANG,
  source_ref: KEY,
  page: 0,
  block_id: "/page/0/Text/1",
  box: [0.05, 0.05, 0.6, 0.1],
  tr_start: 2,
  tr_end: 7,
  tr_prefix: "機器",
  tr_suffix: "。",
};

function trMark(over: Partial<Mark> = {}): Mark {
  return pdfMark({ id: "mt", color: "purple", pieces: [PIECE], lang: LANG, source_ref: KEY, ...over });
}

/** The fake reader with a stand-in for the translation card's selection. */
function Reader() {
  const readerRef = useRef<HTMLDivElement>(null);
  const { ctx, popover } = usePdfMarks({ paperId: 3, readerRef });
  return (
    <div>
      <div ref={readerRef} data-testid="reader" tabIndex={-1}>
        <PdfMarksProvider value={ctx}>
          <div className="react-pdf__Page" data-page-number="1" data-testid="page-1">
            <div className="react-pdf__Page__textContent textLayer">
              <span data-testid="s1">Robots learn </span>
            </div>
            <PdfMarksPage pageNumber={1} />
          </div>
          <CardSelect />
        </PdfMarksProvider>
      </div>
      {popover}
    </div>
  );
}

function CardSelect() {
  const ctx = useContext(PdfMarksContext);
  return (
    <button type="button" onClick={() => ctx?.openCard({ piece: PIECE, rect: { top: 40, bottom: 56, left: 300, right: 500 }, keyboard: false })}>
      select in card
    </button>
  );
}

let create: CreateMock;
beforeEach(() => {
  toastFn.mockReset();
  create = resetStores();
  vi.mocked(listPaperBlocks).mockReset();
  vi.mocked(listPaperBlocks).mockResolvedValue(BLOCKS);
  stubRangeGeometry();
  useTranslationStore.setState({ language: "English", recent: [], enabledByPaper: {}, reveal: null });
});
afterEach(() => {
  window.getSelection()?.removeAllRanges();
});

describe("marks on the PDF translation card", () => {
  it("a selection in the card opens the popover and saves a translation mark filed under its block's section", async () => {
    render(<Reader />);
    layOut();
    fireEvent.click(screen.getByRole("button", { name: "select in card" }));
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      view: "pdf",
      kind: "highlight",
      lang: LANG,
      source_ref: KEY,
      section_key: "1 Introduction",
      pieces: [{ quote: "從示範學習", orig: "Robots learn from demos.", block_id: "/page/0/Text/1", page: 0 }],
    });
  });

  it("the popover sits under the selection in the card, in the window, not at the reader's edge", async () => {
    render(<Reader />);
    layOut();
    // The reader starts at x = 600; the card (and the selection) is left of it.
    place(screen.getByTestId("reader"), 600, 0, 600, 400);
    fireEvent.click(screen.getByRole("button", { name: "select in card" }));
    const bar = await screen.findByRole("toolbar", { name: "Mark the selection" });
    expect(bar.className).toContain("fixed");
    // Centred under the selection (300 to 500), in window coordinates.
    expect(bar.style.left).toBe("230px");
  });

  it("with the highlighter on, a selection in the card marks at once in the pen colour", async () => {
    useMarksStore.setState({ penOn: true, penColor: "pink" });
    render(<Reader />);
    layOut();
    fireEvent.click(screen.getByRole("button", { name: "select in card" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({ color: "pink", lang: LANG, source_ref: KEY });
    expect(useMarksStore.getState().penSession[0]!.places).toEqual([`t:${LANG}:${KEY}`]);
  });

  it("a mark whose words the card isn't showing is a 文 marker on its block; Enter shows it once", async () => {
    resetStores([trMark()]);
    paneBeside();
    const showMark = vi.spyOn(useTranslationStore.getState(), "showMark");
    render(<Reader />);
    layOut();
    const marker = await screen.findByRole("button", { name: "A mark on the 繁中 translation. Show it" });
    expect(marker.textContent).toBe("文");
    expect(marker.tabIndex).toBe(0);
    // A real Enter on the focused marker: one request, not one from the
    // button and another from the page's hit-test.
    marker.focus();
    await userEvent.keyboard("{Enter}");
    expect(showMark).toHaveBeenCalledTimes(1);
    expect(showMark).toHaveBeenCalledWith("mt");
    expect(useTranslationStore.getState().language).toBe(LANG);
    expect(useTranslationStore.getState().reveal).toMatchObject({ markId: "mt" });
    showMark.mockRestore();
  });

  it("a pointer click over the marker is hit-tested on the page, once", async () => {
    resetStores([trMark()]);
    paneBeside();
    const showMark = vi.spyOn(useTranslationStore.getState(), "showMark");
    render(<Reader />);
    layOut();
    const marker = await screen.findByRole("button", { name: "A mark on the 繁中 translation. Show it" });
    place(marker, 362, 40, 18, 18);
    fireEvent.click(screen.getByTestId("s1"), { clientX: 370, clientY: 48, detail: 1 });
    expect(showMark).toHaveBeenCalledTimes(1);
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(true);
    showMark.mockRestore();
  });

  it("with the notes pane hidden, the marker shows the mark in the translation, then opens its note card", async () => {
    resetStores([trMark()]);
    useNotesPaneStore.setState({ open: false });
    useMarkNavStore.setState({ card: null });
    const showMark = vi.spyOn(useTranslationStore.getState(), "showMark");
    render(<Reader />);
    layOut();
    const marker = await screen.findByRole("button", { name: "A mark on the 繁中 translation. Show it" });
    place(marker, 362, 40, 18, 18);
    fireEvent.click(screen.getByTestId("s1"), { clientX: 370, clientY: 48, detail: 1 });
    expect(showMark).toHaveBeenCalledWith("mt");
    expect(useMarkNavStore.getState().card).toBeNull();
    // The reader is done showing it: the card opens on it.
    act(() => useTranslationStore.getState().revealed(useTranslationStore.getState().reveal!.n));
    expect(useMarkNavStore.getState().card).toMatchObject({ paperId: 3, markId: "mt" });
    showMark.mockRestore();
  });

  it("no marker while the open card shows the mark's words", async () => {
    resetStores([trMark()]);
    function Shown() {
      const ctx = useContext(PdfMarksContext);
      return (
        <button type="button" onClick={() => ctx?.setCardShown(new Set(["mt"]))}>
          shown
        </button>
      );
    }
    function Host() {
      const readerRef = useRef<HTMLDivElement>(null);
      const { ctx } = usePdfMarks({ paperId: 3, readerRef });
      return (
        <div ref={readerRef}>
          <PdfMarksProvider value={ctx}>
            <div className="react-pdf__Page" data-page-number="1" data-testid="page-1">
              <PdfMarksPage pageNumber={1} />
            </div>
            <Shown />
          </PdfMarksProvider>
        </div>
      );
    }
    render(<Host />);
    await screen.findByRole("button", { name: /translation. Show it/ });
    fireEvent.click(screen.getByRole("button", { name: "shown" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /translation. Show it/ })).toBeNull());
  });
});

describe("TranslationCard with marks", () => {
  const PLACEMENT: CardPlacement = {
    side: "left",
    top: 100,
    left: 100,
    width: 400,
    maxHeight: 300,
    arrowY: 20,
    arrowX: 0,
    activeFragment: 0,
    scrollFraction: 0,
  };
  const noop = () => undefined;

  it("draws a mark over its words, reports it shown, and passes a selection on", () => {
    const rects = vi
      .spyOn(Range.prototype, "getClientRects")
      .mockImplementation(() => [new DOMRect(120, 140, 60, 18)] as unknown as DOMRectList);
    const onShown = vi.fn();
    const onSelect = vi.fn();
    vi.useFakeTimers();
    render(
      <TranslationCard
        open
        contentKey={KEY}
        language={LANG}
        status="ok"
        html="<p>機器從示範學習。</p>"
        pinned
        placement={PLACEMENT}
        onTogglePin={noop}
        onClose={noop}
        onRetry={noop}
        onMouseEnter={noop}
        onMouseLeave={noop}
        marks={[trMark()]}
        onMarksShown={onShown}
        onSelectText={onSelect}
      />,
    );
    expect(document.querySelectorAll('[data-card-mark="mt"]')).toHaveLength(1);
    expect(onShown).toHaveBeenLastCalledWith(new Set(["mt"]));
    const text = document.querySelector("[data-card-content] p")!.firstChild!;
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, 2);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    fireEvent.mouseUp(document.querySelector("[data-card-content]")!);
    act(() => {
      vi.runAllTimers();
    });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect((onSelect.mock.calls[0]![0] as Range).toString()).toBe("機器");
    vi.useRealTimers();
    rects.mockRestore();
  });

  it("a mark whose words are gone is not drawn (it stays a 文 marker)", () => {
    const onShown = vi.fn();
    render(
      <TranslationCard
        open
        contentKey={KEY}
        language={LANG}
        status="ok"
        html="<p>全新的翻譯。</p>"
        pinned
        placement={PLACEMENT}
        onTogglePin={noop}
        onClose={noop}
        onRetry={noop}
        onMouseEnter={noop}
        onMouseLeave={noop}
        marks={[trMark()]}
        onMarksShown={onShown}
      />,
    );
    expect(document.querySelector("[data-card-mark]")).toBeNull();
    expect(onShown).toHaveBeenLastCalledWith(new Set());
  });
});
