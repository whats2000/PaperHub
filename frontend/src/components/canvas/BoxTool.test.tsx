import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  BLOCKS,
  layOut,
  resetStores,
  stubRangeGeometry,
  type CreateMock,
} from "@/components/canvas/__fixtures__/pdfMarks";
import { Reader } from "@/components/canvas/__fixtures__/pdfMarksReader";
import { BoxToolButton } from "@/components/canvas/BoxTool";
import { listPaperBlocks, locateText } from "@/lib/api";
import { boxMark, textUnderBox, type LayerSpan } from "@/lib/marks/pdfGeometry";
import { useMarksStore } from "@/store/marks";
import type { PaperPageBlock } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listPaperBlocks: vi.fn(),
  locateText: vi.fn(),
}));
const { toastMessage, toastError } = vi.hoisted(() => ({ toastMessage: vi.fn(), toastError: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: toastError, message: toastMessage, success: vi.fn() }),
}));

const span = (text: string, box: [number, number, number, number]): LayerSpan => ({
  text,
  node: document.createTextNode(text),
  box,
});

const EQ: PaperPageBlock = {
  block_id: "/page/0/Equation/3",
  type: "Equation",
  page: 0,
  box: [0.2, 0.3, 0.6, 0.34],
  text: "E = mc^2",
  section: "2 Method",
};
const FIG: PaperPageBlock = {
  block_id: "/page/0/Figure/4",
  type: "Figure",
  page: 0,
  box: [0.1, 0.4, 0.9, 0.8],
  text: "",
  section: "3 Results",
};

let create: CreateMock;

beforeEach(() => {
  create = resetStores();
  vi.mocked(listPaperBlocks).mockReset();
  vi.mocked(listPaperBlocks).mockResolvedValue(BLOCKS);
  vi.mocked(locateText).mockReset();
  toastMessage.mockReset();
  toastError.mockReset();
  stubRangeGeometry();
});

describe("box text and snapping", () => {
  it("takes the text-layer spans under the box, cutting a span it crosses", () => {
    const spans = [
      span("alpha beta", [0.1, 0.1, 0.3, 0.12]),
      span("gamma", [0.1, 0.13, 0.2, 0.15]),
      span("outside", [0.1, 0.5, 0.3, 0.52]),
      span("left right", [0.1, 0.16, 0.5, 0.18]),
    ];
    expect(textUnderBox(spans, [0.05, 0.05, 0.35, 0.19])).toBe("alpha beta gamma left");
  });

  it("a box over more than half an equation snaps to it: an equation mark", () => {
    const res = boxMark({ page: 0, box: [0.25, 0.29, 0.6, 0.35], blocks: [EQ], spans: [], scanned: false });
    expect(res).toEqual({
      kind: "equation",
      section: "2 Method",
      piece: { quote: "E = mc^2", latex: "E = mc^2", page: 0, block_id: EQ.block_id, box: EQ.box },
    });
  });

  it("snaps above exactly half of the equation, never at half", () => {
    // An equation 0.5 wide: a box over 0.25 of it is exactly half.
    const eq: PaperPageBlock = { ...EQ, box: [0.25, 0.5, 0.75, 0.6] };
    const kind = (x1: number) =>
      boxMark({ page: 0, box: [0.25, 0.5, x1, 0.6], blocks: [eq], spans: [], scanned: false }).kind;
    expect(kind(0.5)).toBe("region"); // exactly 50 %
    expect(kind(0.49)).toBe("region"); // just below
    expect(kind(0.51)).toBe("equation"); // just above
    // Half of [0.2, 0.6] in floating point (0.4 - 0.2 over 0.6 - 0.2) is still half.
    expect(boxMark({ page: 0, box: [0.2, 0.3, 0.4, 0.34], blocks: [EQ], spans: [], scanned: false }).kind).toBe("region");
  });

  it("a box over less than half of it stays a region", () => {
    const res = boxMark({
      page: 0,
      box: [0.2, 0.29, 0.38, 0.35],
      blocks: [EQ],
      spans: [span("E =", [0.2, 0.3, 0.3, 0.34])],
      scanned: false,
    });
    expect(res).toEqual({
      kind: "region",
      section: "2 Method",
      piece: { quote: "E =", page: 0, box: [0.2, 0.29, 0.38, 0.35], ocr: false },
    });
  });

  it("on a scanned page the text is Marker's OCR, flagged ocr", () => {
    const table: PaperPageBlock = {
      block_id: "/page/0/Table/2",
      type: "Table",
      page: 0,
      box: [0.1, 0.1, 0.5, 0.2],
      text: "Table 2 accuracy 91.2",
      section: "4 Experiments",
    };
    const res = boxMark({ page: 0, box: [0.05, 0.05, 0.55, 0.25], blocks: [table], spans: [], scanned: true });
    expect(res.piece).toMatchObject({ quote: "Table 2 accuracy 91.2", ocr: true });
    expect(res.section).toBe("4 Experiments");
  });

  it("a box inside a figure marks one panel of it", () => {
    const res = boxMark({ page: 0, box: [0.15, 0.45, 0.45, 0.75], blocks: [FIG], spans: [], scanned: false });
    expect(res).toMatchObject({ kind: "region", section: "3 Results", piece: { block_id: FIG.block_id, panel: true } });
  });
});

describe("BoxToolButton", () => {
  it("toggles the Box tool; Escape stops it", () => {
    render(<BoxToolButton shown />);
    const button = screen.getByRole("button", { name: /Box/ });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(useMarksStore.getState().boxOn).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(useMarksStore.getState().boxOn).toBe(false);
  });
});

/** Drags on the page's marks layer from (x0, y0) to (x1, y1), page pixels. */
function drag(x0: number, y0: number, x1: number, y1: number) {
  const surface = document.querySelector<HTMLElement>("[data-pdf-marks]")!;
  fireEvent.pointerDown(surface, { clientX: x0, clientY: y0, pointerId: 1, button: 0 });
  fireEvent.pointerMove(surface, { clientX: x1, clientY: y1, pointerId: 1 });
  fireEvent.pointerUp(surface, { clientX: x1, clientY: y1, pointerId: 1 });
}

describe("drawing a box", () => {
  it("with Box on, a drag draws a box that quotes the text under it", async () => {
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    act(() => useMarksStore.getState().setBoxOn(true));
    const surface = document.querySelector<HTMLElement>("[data-pdf-marks]")!;
    expect(surface.className).toContain("cursor-crosshair");
    drag(24, 32, 312, 64); // around the first line
    await screen.findByRole("toolbar", { name: "Mark the box" });
    expect(document.querySelector("[data-draft-box]")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Mark: no colour" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "region",
      color: null,
      view: "pdf",
      section_key: "1 Introduction",
      pieces: [{ quote: "Robots learn from demos.", page: 0, box: [0.04, 0.04, 0.52, 0.08], ocr: false }],
    });
  });

  it("on a page with no text layer a drag draws a box at once, its text from OCR", async () => {
    render(<Reader hasText={false} />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    const surface = document.querySelector<HTMLElement>("[data-pdf-marks]")!;
    expect(surface.className).toContain("cursor-crosshair");
    drag(24, 32, 372, 90);
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Mark: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0].pieces[0]).toMatchObject({ quote: "Robots learn from demos.", ocr: true });
  });

  describe("on the compiled PDF of an HTML paper (no Marker blocks)", () => {
    const boxAndSave = async () => {
      render(<Reader />);
      layOut();
      await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
      act(() => useMarksStore.getState().setBoxOn(true));
      drag(24, 32, 312, 64);
      await screen.findByRole("toolbar", { name: "Mark the box" });
      fireEvent.click(screen.getByRole("button", { name: "Mark: no colour" }));
      await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
      return create.mock.calls[0]![0];
    };

    it("finds the box's words in the reading text for its section", async () => {
      vi.mocked(listPaperBlocks).mockResolvedValue([]);
      vi.mocked(locateText).mockResolvedValue({
        match: { chunk_start: 5, chunk_end: 5, chunk_ids: [5], section: "2 Method", text_start: 10, text_end: 34 },
        _meta: {},
      });
      const body = await boxAndSave();
      expect(locateText).toHaveBeenCalledWith(3, "Robots learn from demos.");
      expect(body).toMatchObject({ section_key: "2 Method", pieces: [{ chunk_start: 5, text_start: 10 }] });
      expect(toastMessage).not.toHaveBeenCalled();
    });

    it("saves it without a section, quietly, when its words are not found", async () => {
      vi.mocked(listPaperBlocks).mockResolvedValue([]);
      vi.mocked(locateText).mockResolvedValue({ match: null, _meta: {} });
      const body = await boxAndSave();
      expect(body.section_key).toBeNull();
      expect(toastMessage).not.toHaveBeenCalled();
    });
  });

  describe("when the page layout can't be fetched at save time", () => {
    const open = async () => {
      render(<Reader />);
      layOut();
      await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
      act(() => useMarksStore.getState().setBoxOn(true));
      drag(24, 32, 312, 64);
      await screen.findByRole("toolbar", { name: "Mark the box" });
    };

    it("still saves a box whose result is already in hand", async () => {
      await open();
      await act(async () => {
        await Promise.resolve();
      });
      vi.mocked(listPaperBlocks).mockRejectedValue(new Error("down"));
      fireEvent.click(screen.getByRole("button", { name: "Mark: Key result" }));
      await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
      expect(create.mock.calls[0]![0]).toMatchObject({ kind: "region", view: "pdf" });
    });

    it("saves nothing and says so when no result was worked out", async () => {
      vi.mocked(listPaperBlocks).mockRejectedValue(new Error("down"));
      await open();
      fireEvent.click(screen.getByRole("button", { name: "Mark: Key result" }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      expect(create).not.toHaveBeenCalled();
    });
  });

  it("a click without a drag draws nothing", async () => {
    act(() => useMarksStore.getState().setBoxOn(true));
    render(<Reader />);
    layOut();
    drag(100, 100, 101, 101);
    await act(() => new Promise((r) => setTimeout(r, 0)));
    expect(screen.queryByRole("toolbar")).toBeNull();
  });
});

describe("Escape, layer by layer", () => {
  it("the first Escape closes the box's popover and gives focus back; the second stops Box", async () => {
    render(
      <>
        <BoxToolButton shown />
        <Reader />
      </>,
    );
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    act(() => useMarksStore.getState().setBoxOn(true));
    drag(24, 32, 312, 64);
    const bar = await screen.findByRole("toolbar", { name: "Mark the box" });
    bar.querySelector("button")!.focus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape", bubbles: true });
    await waitFor(() => expect(bar.dataset.state).toBe("closed"));
    expect(useMarksStore.getState().boxOn).toBe(true);
    expect(document.activeElement).toBe(screen.getByTestId("reader"));
    fireEvent.keyDown(document.activeElement!, { key: "Escape", bubbles: true });
    expect(useMarksStore.getState().boxOn).toBe(false);
  });

  it("an Escape another layer handled, or one while a dialog is open, leaves Box on", () => {
    render(<BoxToolButton shown />);
    act(() => useMarksStore.getState().setBoxOn(true));
    const handled = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    handled.preventDefault();
    act(() => {
      document.body.dispatchEvent(handled);
    });
    expect(useMarksStore.getState().boxOn).toBe(true);
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(useMarksStore.getState().boxOn).toBe(true);
    dialog.remove();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(useMarksStore.getState().boxOn).toBe(false);
  });
});
