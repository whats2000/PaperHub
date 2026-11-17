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

vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), listPaperBlocks: vi.fn() }));
const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: vi.fn(), message: vi.fn(), success: vi.fn() }),
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
    useNotesPaneStore.setState({ openAt });
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
