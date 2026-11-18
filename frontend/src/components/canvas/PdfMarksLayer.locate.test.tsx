/** PDF pages with no Marker blocks — the LaTeX-compiled PDF of an HTML
 * paper: a mark's section and chunks, and Follow's section, come from
 * locating the page's text in the paper's reading text. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { layOut, place, resetStores, stubRangeGeometry, type CreateMock } from "@/components/canvas/__fixtures__/pdfMarks";
import { Reader } from "@/components/canvas/__fixtures__/pdfMarksReader";
import { listPaperBlocks, locateText, type LocateResult } from "@/lib/api";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listPaperBlocks: vi.fn(),
  locateText: vi.fn(),
}));
const { toastFn, toastMessage, toastError } = vi.hoisted(() => ({ toastFn: vi.fn(), toastMessage: vi.fn(), toastError: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: toastError, message: toastMessage, success: vi.fn() }),
}));

const HIT: LocateResult = {
  match: { chunk_start: 102, chunk_end: 103, chunk_ids: [102, 103], section: "2 Method", text_start: 40, text_end: 75 },
  _meta: { matched: true },
};
const MISS: LocateResult = { match: null, _meta: { matched: false, reason: "no_start" } };

/** Scrolls the fake page (and its spans) to `top`. */
function scrollTo(top: number) {
  place(screen.getByTestId("page-1"), 0, top, 600, 800);
  place(screen.getByTestId("s1"), 30, top + 40, 120, 16);
  place(screen.getByTestId("s2"), 150, top + 40, 150, 16);
  place(screen.getByTestId("s3"), 30, top + 90, 170, 16);
  const s4 = screen.queryByTestId("s4");
  if (s4) place(s4, 30, top + 140, 170, 16);
  fireEvent.scroll(screen.getByTestId("reader"));
}

function select(from: string, to: string) {
  const range = document.createRange();
  range.setStart(screen.getByTestId(from).firstChild!, 0);
  const end = screen.getByTestId(to).firstChild as Text;
  range.setEnd(end, end.length);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  fireEvent.mouseUp(screen.getByTestId(to));
}

let create: CreateMock;

beforeEach(() => {
  toastFn.mockReset();
  toastMessage.mockReset();
  toastError.mockReset();
  create = resetStores();
  vi.mocked(listPaperBlocks).mockReset();
  vi.mocked(listPaperBlocks).mockResolvedValue([]); // no blocks.json
  vi.mocked(locateText).mockReset();
  stubRangeGeometry();
});
afterEach(() => window.getSelection()?.removeAllRanges());

describe("marks on a PDF without Marker blocks", () => {
  it("file under the section found in the reading text, and keep its chunks", async () => {
    vi.mocked(locateText).mockResolvedValue(HIT);
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    select("s1", "s3");
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    // The text as laid out, a line break between lines.
    expect(locateText).toHaveBeenCalledWith(3, "Robots learn from demos.\nNext line here.");
    expect(create.mock.calls[0]![0]).toMatchObject({
      section_key: "2 Method",
      pieces: [{ quote: "Robots learn from demos. Next line here.", page: 0, chunk_start: 102, chunk_end: 103, text_start: 40, text_end: 75 }],
    });
    expect(toastMessage).not.toHaveBeenCalled();
  });

  it("send the page text around a selection, which says where a few common words are", async () => {
    vi.mocked(locateText).mockResolvedValue(HIT);
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    select("s2", "s2");
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(locateText).toHaveBeenCalledWith(3, "from demos.", { before: "Robots learn ", after: "\nNext line here." });
  });

  it("not found: saved with no section, and the toast says so", async () => {
    vi.mocked(locateText).mockResolvedValue(MISS);
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    select("s1", "s2");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0].section_key).toBeNull();
    expect(create.mock.calls[0]![0].pieces[0]).not.toHaveProperty("chunk_start");
    await waitFor(() => expect(toastMessage).toHaveBeenCalledWith(expect.stringContaining("wasn't found")));
  });
});

describe("Follow on a PDF without Marker blocks", () => {
  it("locates the top lines of the view; a miss keeps the last section", async () => {
    vi.mocked(locateText).mockResolvedValueOnce(HIT).mockResolvedValue(MISS);
    const onReading = vi.fn();
    render(<Reader onReading={onReading} />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    scrollTo(-30);
    await waitFor(() => expect(onReading).toHaveBeenLastCalledWith("2 Method"));
    expect(locateText).toHaveBeenCalledWith(3, "Robots learn from demos.\nNext line here.");
    // Further down: the next line is not found; the section stays.
    scrollTo(-70);
    await waitFor(() => expect(locateText).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 20));
    expect(onReading).toHaveBeenLastCalledWith("2 Method");
    expect(onReading).not.toHaveBeenCalledWith(null);
  });
});

describe("Follow on a PDF without Marker blocks, with more text below", () => {
  it("sends the lines below the top ones as context", async () => {
    vi.mocked(locateText).mockResolvedValue(HIT);
    const onReading = vi.fn();
    render(<Reader onReading={onReading} thirdLine="Third line below." />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    scrollTo(-30);
    await waitFor(() => expect(onReading).toHaveBeenLastCalledWith("2 Method"));
    expect(locateText).toHaveBeenCalledWith(3, "Robots learn from demos.\nNext line here.", {
      before: "",
      after: "Third line below.",
    });
  });
});

describe("a page whose blocks failed to load", () => {
  it("says so and saves nothing, rather than locating and finding nothing; a retry saves", async () => {
    vi.mocked(listPaperBlocks).mockRejectedValue(new Error("503"));
    render(<Reader />);
    layOut();
    await waitFor(() => expect(listPaperBlocks).toHaveBeenCalled());
    select("s1", "s2");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(expect.stringContaining("layout")));
    expect(locateText).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
    expect(toastMessage).not.toHaveBeenCalled();
    // The blocks load on the next try: the mark files by its block.
    vi.mocked(listPaperBlocks).mockResolvedValue([
      { block_id: "/page/0/Text/1", type: "Text", page: 0, box: [0.05, 0.05, 0.6, 0.1], text: "Robots learn from demos.", section: "1 Introduction" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0].section_key).toBe("1 Introduction");
    expect(locateText).not.toHaveBeenCalled();
  });
});
