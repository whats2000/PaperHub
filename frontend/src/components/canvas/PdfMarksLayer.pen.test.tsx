import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BLOCKS, layOut, pdfMark, resetStores, stubRangeGeometry, type CreateMock } from "@/components/canvas/__fixtures__/pdfMarks";
import { Reader } from "@/components/canvas/__fixtures__/pdfMarksReader";
import { listPaperBlocks, locateText } from "@/lib/api";
import { resetMarkOps, useMarksStore } from "@/store/marks";
import type { Mark, MarkIn, PaperPageBlock } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listPaperBlocks: vi.fn(),
  locateText: vi.fn(),
}));
const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: vi.fn(), message: vi.fn(), success: vi.fn() }),
}));

const FIG: PaperPageBlock = {
  block_id: "/page/0/Figure/4",
  type: "Figure",
  page: 0,
  box: [0.1, 0.3, 0.9, 0.6],
  text: "",
  section: "3 Results",
};

/** A stroke over spans `from`..`to` on the text layer. */
function stroke(from: string, fromAt: number, to: string, toAt: number) {
  const range = document.createRange();
  range.setStart(screen.getByTestId(from).firstChild!, fromAt);
  range.setEnd(screen.getByTestId(to).firstChild!, toAt);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  fireEvent.mouseUp(screen.getByTestId(to));
}

/** A click (no drag) at page pixels (x, y). */
function clickAt(x: number, y: number, on = "s1") {
  const el = screen.getByTestId(on);
  fireEvent.pointerDown(el, { clientX: x, clientY: y });
  fireEvent.click(el, { clientX: x, clientY: y });
}

let create: CreateMock;
let erase: ReturnType<typeof vi.fn<(id: string, index: number) => Promise<void>>>;

beforeEach(() => {
  resetMarkOps();
  vi.mocked(locateText).mockReset();
  vi.mocked(locateText).mockResolvedValue({ match: null, _meta: {} });
  toastFn.mockReset();
  create = resetStores();
  // The server: a new mark per call, or the joined one with the piece added.
  let n = 0;
  create.mockImplementation((body: MarkIn) => {
    const list = useMarksStore.getState().marksByPaper[3] ?? [];
    const joined = body.join_to ? list.find((m) => m.id === body.join_to) : undefined;
    const m: Mark = joined
      ? { ...joined, pieces: [...joined.pieces, ...body.pieces] }
      : pdfMark({ id: `mk_${++n}`, kind: body.kind, color: body.color, pieces: body.pieces, section_key: body.section_key });
    useMarksStore.setState((s) => ({ marksByPaper: { 3: [...(s.marksByPaper[3] ?? []).filter((x) => x.id !== m.id), m] } }));
    return Promise.resolve({
      mark: m,
      block: null,
      saved_to: { note_id: 7, paper_title: "Duo", section: body.section_key, part_index: 0, part_count: 1 },
    });
  });
  erase = vi.fn(() => Promise.resolve());
  useMarksStore.setState({ erase });
  vi.mocked(listPaperBlocks).mockReset();
  vi.mocked(listPaperBlocks).mockResolvedValue([...BLOCKS, FIG]);
  stubRangeGeometry();
});

afterEach(() => {
  window.getSelection()?.removeAllRanges();
});

describe("the highlighter on a PDF page", () => {
  it("marks a stroke at once, and joins the next stroke in the same Marker block", async () => {
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    act(() => useMarksStore.getState().setPen(true, "purple"));
    stroke("s1", 0, "s1", "Robots".length);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      view: "pdf",
      kind: "highlight",
      color: "purple",
      section_key: "1 Introduction",
      pieces: [{ quote: "Robots", block_id: "/page/0/Text/1" }],
    });
    expect(screen.queryByRole("toolbar")).toBeNull();
    stroke("s2", 0, "s2", "from demos.".length);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]![0]).toMatchObject({ join_to: "mk_1", pieces: [{ quote: "from demos." }] });
    await waitFor(() => expect(toastFn).toHaveBeenLastCalledWith("Joined to the same mark", expect.anything()));
    // Another block: a mark of its own.
    stroke("s3", 0, "s3", "Next".length);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[2]![0].join_to).toBeUndefined();
  });

  it("a pen click on a mark erases the piece under it", () => {
    resetStores([
      pdfMark({
        pieces: [
          { quote: "Robots", page: 0, block_id: "/page/0/Text/1", rects: [[0.05, 0.05, 0.15, 0.07]] },
          { quote: "demos.", page: 0, block_id: "/page/0/Text/1", rects: [[0.35, 0.05, 0.5, 0.07]] },
        ],
      }),
    ]);
    useMarksStore.setState({ erase });
    render(<Reader />);
    layOut();
    act(() => useMarksStore.getState().setPen(true));
    // Two boxes: the words between them are not marked.
    expect(document.querySelectorAll('[data-mark="mk_1"]')).toHaveLength(2);
    // Hovered, the piece shows the dashed ring, faded in.
    fireEvent.pointerMove(screen.getByTestId("s2"), { clientX: 240, clientY: 48 });
    const box = document.querySelectorAll<HTMLElement>('[data-mark="mk_1"]')[1]!;
    expect(box.dataset.erase).toBe("true");
    expect(box.style.outline).toContain("dashed");
    expect(box.className).toContain("outline-color");
    clickAt(240, 48, "s2");
    expect(erase).toHaveBeenCalledWith("mk_1", 1);
  });

  it("a pen click on a figure marks it whole in the pen colour", async () => {
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    await waitFor(() => expect(document.querySelector('[data-object-mark="figure"]')).not.toBeNull());
    act(() => useMarksStore.getState().setPen(true, "blue"));
    // The corner button gives way to the click.
    expect(document.querySelector('[data-object-mark="figure"]')!.getAttribute("aria-hidden")).toBe("true");
    clickAt(300, 360);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "figure",
      color: "blue",
      section_key: "3 Results",
      pieces: [{ page: 0, block_id: "/page/0/Figure/4", box: FIG.box }],
    });
  });

  it("a box drawn on a scanned page marks at once in the pen colour", async () => {
    render(<Reader hasText={false} />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    act(() => useMarksStore.getState().setPen(true, "pink"));
    const surface = document.querySelector<HTMLElement>("[data-pdf-marks]")!;
    fireEvent.pointerDown(surface, { clientX: 24, clientY: 32, pointerId: 1, button: 0 });
    fireEvent.pointerMove(surface, { clientX: 372, clientY: 90, pointerId: 1 });
    fireEvent.pointerUp(surface, { clientX: 372, clientY: 90, pointerId: 1 });
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({ kind: "region", color: "pink" });
    expect(create.mock.calls[0]![0].pieces[0]).toMatchObject({ ocr: true });
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("with the pen off, a selection opens the popover again", async () => {
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    act(() => useMarksStore.getState().setPen(true));
    act(() => useMarksStore.getState().setPen(false));
    stroke("s1", 0, "s1", "Robots".length);
    expect(await screen.findByRole("toolbar", { name: "Mark the selection" })).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("on a page without Marker blocks, strokes join when they locate into the same paragraph", async () => {
    vi.mocked(listPaperBlocks).mockResolvedValue([]);
    // Where each stroke's text is in the paper's reading text.
    const where: Record<string, { paragraph: number; start: number } | null> = {
      Robots: { paragraph: 5, start: 10 },
      "from demos.": { paragraph: 5, start: 24 },
      Next: { paragraph: 6, start: 60 },
      "line here.": null,
    };
    vi.mocked(locateText).mockImplementation((_id, text) => {
      const at = where[text.trim()];
      return Promise.resolve({
        match: at
          ? {
              chunk_start: 101,
              chunk_end: 101,
              chunk_ids: [101],
              section: "1 Introduction",
              text_start: at.start,
              text_end: at.start + text.length,
              paragraph: at.paragraph,
            }
          : null,
        _meta: {},
      });
    });
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke("s1", 0, "s1", "Robots".length);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0].pieces[0]).toMatchObject({ quote: "Robots", text_start: 10 });
    stroke("s2", 0, "s2", "from demos.".length);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]![0].join_to).toBe("mk_1");
    // Another paragraph: its own mark.
    stroke("s3", 0, "s3", "Next".length);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[2]![0].join_to).toBeUndefined();
    // Not located: never joins (not even the mark just above).
    stroke("s3", "Next ".length, "s3", "Next line here.".length);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(4));
    expect(create.mock.calls[3]![0].join_to).toBeUndefined();
  });
});
