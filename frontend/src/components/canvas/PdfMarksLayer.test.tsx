import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BLOCKS,
  layOut,
  pdfMark,
  place,
  resetStores,
  stubRangeGeometry,
  type CreateMock,
} from "@/components/canvas/__fixtures__/pdfMarks";
import { Reader } from "@/components/canvas/__fixtures__/pdfMarksReader";
import { selectionToPdfPieces } from "@/lib/marks/pdfGeometry";
import { listPaperBlocks } from "@/lib/api";
import { useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useCanvasStore } from "@/store/canvas";
import { useMarkNavStore } from "@/store/markNav";
import { REVEAL_WAIT_MS, useTranslationStore } from "@/store/translation";

vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), listPaperBlocks: vi.fn() }));
const { toastFn, toastMessage } = vi.hoisted(() => ({ toastFn: vi.fn(), toastMessage: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: vi.fn(), message: toastMessage, success: vi.fn() }),
}));

function selectSpans(from: string, fromAt: number, to: string, toAt: number): Range {
  const range = document.createRange();
  range.setStart(screen.getByTestId(from).firstChild!, fromAt);
  range.setEnd(screen.getByTestId(to).firstChild!, toAt);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  return range;
}

let create: CreateMock;

beforeEach(() => {
  toastFn.mockReset();
  toastMessage.mockReset();
  create = resetStores();
  vi.mocked(listPaperBlocks).mockReset();
  vi.mocked(listPaperBlocks).mockResolvedValue(BLOCKS);
  stubRangeGeometry();
});

afterEach(() => {
  window.getSelection()?.removeAllRanges();
});

describe("selectionToPdfPieces", () => {
  it("keeps line boxes as page fractions and the Marker block that holds the selection", () => {
    render(<Reader />);
    layOut();
    const range = selectSpans("s1", 0, "s2", "from demos.".length);
    expect(selectionToPdfPieces(range, screen.getByTestId("reader"), { 0: BLOCKS })).toEqual([
      {
        quote: "Robots learn from demos.",
        raw: "Robots learn from demos.",
        page: 0,
        block_id: "/page/0/Text/1",
        section: "1 Introduction",
        rects: [[0.05, 0.05, 0.5, 0.07]],
        // The page text around it (none before: it starts the page).
        before: "",
        after: "\nNext line here.",
      },
    ]);
  });

  it("cuts a span the selection starts inside, and makes one piece per block", () => {
    render(<Reader />);
    layOut();
    const range = selectSpans("s1", "Robots ".length, "s3", "Next line".length);
    const pieces = selectionToPdfPieces(range, screen.getByTestId("reader"), { 0: BLOCKS });
    expect(pieces.map((p) => [p.quote, p.block_id])).toEqual([
      ["learn from demos.", "/page/0/Text/1"],
      ["Next line", "/page/0/Text/2"],
    ]);
    // "Robots " is 7 of the span's 13 characters: the box starts there.
    expect(pieces[0]!.rects[0]![0]).toBeCloseTo((30 + (120 * 7) / 13) / 600, 3);
    // The text around the selection rides on its first and last piece.
    expect([pieces[0]!.before, pieces[0]!.after, pieces[1]!.before, pieces[1]!.after]).toEqual([
      "Robots ", undefined, undefined, " here.",
    ]);
  });

  it("without Marker blocks a piece has no block and no section", () => {
    render(<Reader />);
    layOut();
    const [piece] = selectionToPdfPieces(selectSpans("s1", 0, "s1", 6), screen.getByTestId("reader"), {});
    expect(piece).toMatchObject({ quote: "Robots", page: 0, block_id: null, section: null });
  });
});

describe("PDF text marks", () => {
  it("a selection opens the popover; a highlight saves one mark per section", async () => {
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalledWith(3, 0));
    selectSpans("s1", "Robots ".length, "s3", "Next line".length);
    fireEvent.mouseUp(screen.getByTestId("s3"));
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls.map((c) => [c[0].view, c[0].kind, c[0].color, c[0].section_key])).toEqual([
      ["pdf", "highlight", "green", "1 Introduction"],
      ["pdf", "highlight", "green", "2 Method"],
    ]);
    const piece = create.mock.calls[0]![0].pieces[0]!;
    expect(piece).toMatchObject({ quote: "learn from demos.", page: 0, block_id: "/page/0/Text/1" });
    expect(piece.rects).toHaveLength(1);
    // The section rides on the mark, not on the stored piece.
    expect(piece).not.toHaveProperty("section");
    await waitFor(() => expect(toastFn).toHaveBeenCalledTimes(1));
    expect(window.getSelection()!.isCollapsed).toBe(true);
  });

  it("saves what was selected even after the text layer re-renders", async () => {
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalledWith(3, 0));
    selectSpans("s1", 0, "s2", "from demos.".length);
    fireEvent.mouseUp(screen.getByTestId("s2"));
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    // A resize re-renders the text layer: the selected text nodes go.
    screen.getByTestId("s1").firstChild!.remove();
    screen.getByTestId("s2").firstChild!.remove();
    fireEvent.click(screen.getByRole("button", { name: "Quote to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      section_key: "1 Introduction",
      pieces: [{ quote: "Robots learn from demos.", page: 0, block_id: "/page/0/Text/1", rects: [[0.05, 0.05, 0.5, 0.07]] }],
    });
  });

  it("draws marks on a layer that takes no pointer events, hit-tested on click", () => {
    resetStores([
      pdfMark({
        pieces: [{ quote: "Robots", page: 0, block_id: "/page/0/Text/1", rects: [[0.05, 0.05, 0.25, 0.07]] }],
      }),
    ]);
    const openAt = vi.fn();
    // The notes pane beside the reader: a click opens the mark's block there.
    useNotesPaneStore.setState({ openAt, open: true });
    useCanvasStore.setState({ open: true, width: 900 });
    render(<Reader />);
    layOut();
    const layer = document.querySelector<HTMLElement>("[data-pdf-marks]")!;
    expect(layer.className).toContain("pointer-events-none");
    const box = layer.querySelector<HTMLElement>('[data-mark="mk_1"]')!;
    expect(box.style.left).toBe("5%");
    expect(box.style.width).toBe("20%");
    // A press on the text under the mark is the text's: selection still works.
    expect(fireEvent.mouseDown(screen.getByTestId("s1"), { clientX: 60, clientY: 45 })).toBe(true);
    fireEvent.pointerDown(screen.getByTestId("s1"), { clientX: 60, clientY: 45 });
    fireEvent.click(screen.getByTestId("s1"), { clientX: 60, clientY: 45 });
    expect(openAt).toHaveBeenCalledWith({ kind: "paper", id: 3, noteId: 7, blockId: "nb1" });
    // Off the mark, nothing opens.
    openAt.mockReset();
    fireEvent.pointerDown(screen.getByTestId("s3"), { clientX: 60, clientY: 95 });
    fireEvent.click(screen.getByTestId("s3"), { clientX: 60, clientY: 95 });
    expect(openAt).not.toHaveBeenCalled();
  });

  it("opens the note card on a click when the notes pane is hidden", () => {
    resetStores([
      pdfMark({
        pieces: [{ quote: "Robots", page: 0, block_id: "/page/0/Text/1", rects: [[0.05, 0.05, 0.25, 0.07]] }],
      }),
    ]);
    const openAt = vi.fn();
    useNotesPaneStore.setState({ openAt, open: false });
    useMarkNavStore.setState({ card: null });
    const outside = vi.fn();
    window.addEventListener("click", outside);
    render(<Reader />);
    layOut();
    fireEvent.pointerDown(screen.getByTestId("s1"), { clientX: 60, clientY: 45 });
    fireEvent.click(screen.getByTestId("s1"), { clientX: 60, clientY: 45 });
    expect(openAt).not.toHaveBeenCalled();
    expect(useMarkNavStore.getState().card).toMatchObject({ paperId: 3, markId: "mk_1" });
    // The click stops at the page: the card does not take it for a click outside.
    expect(outside).not.toHaveBeenCalled();
    window.removeEventListener("click", outside);
  });

  it("hides the marks when Show my marks is off", () => {
    resetStores([pdfMark({ kind: "quote", color: null, pieces: [{ quote: "Robots", page: 0, rects: [[0.05, 0.05, 0.25, 0.07]] }] })]);
    render(<Reader />);
    const layer = document.querySelector<HTMLElement>("[data-pdf-marks]")!;
    expect(layer.dataset.hidden).toBeUndefined();
    act(() => {
      useMarksStore.setState({ showMarks: false });
    });
    expect(layer.dataset.hidden).toBe("true");
  });
});

describe("Follow on a PDF", () => {
  it("reports the section of the Marker block at the top of the view", async () => {
    const onReading = vi.fn();
    render(<Reader onReading={onReading} />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    // Scrolled so the first block straddles the top of the view.
    place(screen.getByTestId("page-1"), 0, -50, 600, 800);
    fireEvent.scroll(screen.getByTestId("reader"));
    await waitFor(() => expect(onReading).toHaveBeenLastCalledWith("1 Introduction"));
    // Further down: the first block has gone above the top.
    place(screen.getByTestId("page-1"), 0, -90, 600, 800);
    fireEvent.scroll(screen.getByTestId("reader"));
    await waitFor(() => expect(onReading).toHaveBeenLastCalledWith("2 Method"));
  });
});

describe("marks made in the HTML view", () => {
  const htmlMark = (id: string, quote: string) =>
    pdfMark({
      id,
      view: "html",
      kind: "highlight",
      color: "green",
      pieces: [{ quote, chunk_start: 101, chunk_end: 101, prefix: "", suffix: "" }],
    });

  it("show on the PDF wherever their quote is found in the text layer, math or not", async () => {
    resetStores([htmlMark("mh", "learn from demos."), htmlMark("mm", "Robots $x$ from demos. Next")]);
    render(<Reader />);
    layOut();
    await waitFor(() => expect(document.querySelector('[data-mark="mh"]')).not.toBeNull());
    const box = document.querySelector<HTMLElement>('[data-mark="mh"]')!;
    // "Robots " is 7 of the first span's 13 characters.
    expect(Number.parseFloat(box.style.left)).toBeCloseTo(((30 + (120 * 7) / 13) / 600) * 100, 1);
    expect(Number.parseFloat(box.style.width)).toBeCloseTo(((300 - (30 + (120 * 7) / 13)) / 600) * 100, 1);
    // Math in the quote is a gap; the match runs over two lines.
    expect(document.querySelectorAll('[data-mark="mm"]')).toHaveLength(2);
  });

  it("are not drawn where the quote is not on the page", async () => {
    resetStores([htmlMark("mh", "learn from demos."), htmlMark("mx", "words that are not on this page")]);
    render(<Reader />);
    layOut();
    await waitFor(() => expect(document.querySelector('[data-mark="mh"]')).not.toBeNull());
    expect(document.querySelector('[data-mark="mx"]')).toBeNull();
  });
});

describe("showing a mark asked for (a source chip, My marks)", () => {
  afterEach(() => {
    vi.useRealTimers();
    useTranslationStore.setState({ reveal: null });
  });

  function ask(markId: string) {
    act(() => useTranslationStore.getState().requestReveal(3, markId, { fallback: true }));
  }

  it("flashes a mark the page draws", () => {
    resetStores([pdfMark({ pieces: [{ quote: "Robots", page: 0, rects: [[0.05, 0.05, 0.25, 0.07]] }] })]);
    render(<Reader />);
    layOut();
    ask("mk_1");
    expect(useTranslationStore.getState().reveal).toBeNull();
    expect(document.querySelector<HTMLElement>('[data-mark="mk_1"]')!.style.boxShadow).toContain("3px");
  });

  it("waits for the mark's page to render before trying the HTML view", () => {
    vi.useFakeTimers();
    // On page 2, which hasn't rendered.
    resetStores([pdfMark({ pieces: [{ quote: "Later words", page: 1, rects: [[0.1, 0.1, 0.4, 0.12]] }] })]);
    useCanvasStore.setState({ readerTabRequest: null });
    render(<Reader />);
    ask("mk_1");
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(useCanvasStore.getState().readerTabRequest).toBeNull();
    expect(useTranslationStore.getState().reveal).not.toBeNull();
    // Never rendered, within reason: it says so, and stays on the PDF.
    act(() => {
      vi.advanceTimersByTime(REVEAL_WAIT_MS);
    });
    expect(useTranslationStore.getState().reveal).toBeNull();
    expect(useCanvasStore.getState().readerTabRequest).toBeNull();
    expect(toastMessage).toHaveBeenCalledWith("This mark isn't drawn in the paper here.");
  });

  it("tries the HTML view for a passage its rendered page doesn't draw", () => {
    vi.useFakeTimers();
    resetStores([pdfMark({ pieces: [{ quote: "Robots", page: 0 }] })]);
    useCanvasStore.setState({ readerTabRequest: null });
    render(<Reader />);
    ask("mk_1");
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(useCanvasStore.getState().readerTabRequest).toBeNull();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(useCanvasStore.getState().readerTabRequest).toMatchObject({ paperId: 3, tab: "html" });
  });

  it("never sends a box, figure or region to the HTML view", () => {
    vi.useFakeTimers();
    resetStores([pdfMark({ kind: "region", pieces: [{ quote: "a region", page: 0 }] })]);
    useCanvasStore.setState({ readerTabRequest: null });
    render(<Reader />);
    ask("mk_1");
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(useCanvasStore.getState().readerTabRequest).toBeNull();
    expect(toastMessage).toHaveBeenCalledWith("This mark isn't drawn in the paper here.");
  });
});
