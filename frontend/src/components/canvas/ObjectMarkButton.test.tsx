import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BLOCKS,
  layOut,
  resetStores,
  stubRangeGeometry,
  type CreateMock,
} from "@/components/canvas/__fixtures__/pdfMarks";
import { Reader } from "@/components/canvas/__fixtures__/pdfMarksReader";
import { useHtmlMarks } from "@/components/canvas/useHtmlMarks";
import { listPaperBlocks } from "@/lib/api";
import { chunkMap } from "@/lib/marks/anchor";
import { useMarksStore } from "@/store/marks";
import type { PaperPageBlock } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), listPaperBlocks: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), message: vi.fn(), success: vi.fn() }),
}));

const FIG: PaperPageBlock = {
  block_id: "/page/0/Figure/4",
  type: "Figure",
  page: 0,
  box: [0.1, 0.3, 0.9, 0.6],
  text: "",
  section: "3 Results",
};
const CAPTION: PaperPageBlock = {
  block_id: "/page/0/Caption/5",
  type: "Caption",
  page: 0,
  box: [0.1, 0.61, 0.9, 0.65],
  text: "Figure 1: A robot arm.",
  section: "3 Results",
};
const EQ: PaperPageBlock = {
  block_id: "/page/0/Equation/6",
  type: "Equation",
  page: 0,
  box: [0.2, 0.7, 0.6, 0.74],
  text: "E = mc^2",
  section: "3 Results",
};

let create: CreateMock;

beforeEach(() => {
  create = resetStores();
  vi.mocked(listPaperBlocks).mockReset();
  vi.mocked(listPaperBlocks).mockResolvedValue([...BLOCKS, FIG, CAPTION, EQ]);
  stubRangeGeometry();
});

describe("the corner mark button on PDF pages", () => {
  it("fades in over a figure and marks it whole in one choice", async () => {
    render(<Reader />);
    layOut();
    const button = await screen.findByRole("button", { name: "Mark figure" });
    expect(button.dataset.shown).toBe("false");
    fireEvent.pointerMove(screen.getByTestId("page-1"), { clientX: 300, clientY: 360 });
    expect(button.dataset.shown).toBe("true");
    fireEvent.pointerMove(screen.getByTestId("page-1"), { clientX: 300, clientY: 20 });
    expect(button.dataset.shown).toBe("false");
    fireEvent.click(button);
    await screen.findByRole("toolbar", { name: "Mark the figure" });
    fireEvent.click(screen.getByRole("button", { name: "Add to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "figure",
      color: null,
      view: "pdf",
      section_key: "3 Results",
      pieces: [{ quote: "Figure 1: A robot arm.", page: 0, block_id: FIG.block_id, box: FIG.box }],
    });
  });

  it("marks an equation with its LaTeX and box", async () => {
    render(<Reader />);
    layOut();
    fireEvent.click(await screen.findByRole("button", { name: "Mark equation" }));
    await screen.findByRole("toolbar", { name: "Mark the equation" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Method" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "equation",
      color: "blue",
      pieces: [{ quote: "E = mc^2", latex: "E = mc^2", page: 0, block_id: EQ.block_id, box: EQ.box }],
    });
  });

  it("stays on a scanned page, where a drag draws a box", async () => {
    render(<Reader hasText={false} />);
    layOut();
    const button = await screen.findByRole("button", { name: "Mark figure" });
    expect(button.getAttribute("aria-hidden")).toBeNull();
    fireEvent.pointerMove(screen.getByTestId("page-1"), { clientX: 300, clientY: 360 });
    expect(button.dataset.shown).toBe("true");
    fireEvent.click(button);
    await screen.findByRole("toolbar", { name: "Mark the figure" });
    fireEvent.click(screen.getByRole("button", { name: "Add to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0].kind).toBe("figure");
  });

  it("is hidden while the Box tool is on", async () => {
    render(<Reader />);
    layOut();
    const button = await screen.findByRole("button", { name: "Mark figure" });
    act(() => useMarksStore.getState().setBoxOn(true));
    expect(button.getAttribute("aria-hidden")).toBe("true");
  });
});

const PAPER = `
  <p><span id="phchunk-0"></span>Robots learn from demonstrations.</p>
  <span id="phfig-001"></span><figure><img alt="arm"><figcaption>Figure 1: Robot arm.</figcaption></figure>
  <p>The energy is <span class="math display">\\[E = mc^2\\]</span></p>
`;

function frameDoc(body: string): Document {
  const frame = document.createElement("iframe");
  document.body.appendChild(frame);
  const doc = frame.contentDocument!;
  doc.body.innerHTML = body;
  const FrameRange = (frame.contentWindow as unknown as { Range: typeof Range }).Range;
  FrameRange.prototype.getClientRects = () => [] as unknown as DOMRectList;
  FrameRange.prototype.getBoundingClientRect = () => new DOMRect(10, 40, 100, 16);
  const box = (sel: string, r: DOMRect) => {
    (doc.querySelector(sel) as HTMLElement).getBoundingClientRect = () => r;
  };
  box("figure", new DOMRect(20, 100, 300, 200));
  box(".math.display", new DOMRect(60, 320, 200, 30));
  return doc;
}

function Host({ doc }: { doc: Document }) {
  return <div>{useHtmlMarks({ paperId: 3, doc, isDark: false })}</div>;
}

describe("the corner mark button in the HTML reader", () => {
  beforeEach(() => {
    useMarksStore.setState({
      ensureChunks: vi.fn(() => Promise.resolve(chunkMap([{ id: 101, dom_id: "phchunk-0", section: "1 Introduction" }]))),
    });
  });
  afterEach(() => document.querySelectorAll("iframe").forEach((f) => f.remove()));

  it("marks a figure whole, tied to its figure id", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    const button = await screen.findByRole("button", { name: "Mark figure" });
    act(() => {
      doc.querySelector("img")!.dispatchEvent(new MouseEvent("mousemove", { bubbles: true }));
    });
    expect(button.dataset.shown).toBe("true");
    fireEvent.click(button);
    await screen.findByRole("toolbar", { name: "Mark the figure" });
    fireEvent.click(screen.getByRole("button", { name: "Add to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "figure",
      color: null,
      view: "html",
      section_key: "1 Introduction",
      pieces: [{ quote: "Figure 1: Robot arm.", figure_id: "fig-001", chunk_start: 101 }],
    });
  });

  it("marks a display equation with its LaTeX from the paper's source", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark equation" }));
    await screen.findByRole("toolbar", { name: "Mark the equation" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "equation",
      color: "green",
      view: "html",
      pieces: [{ quote: "$E = mc^2$", latex: "E = mc^2", latex_from: "source", chunk_start: 101 }],
    });
  });
});
