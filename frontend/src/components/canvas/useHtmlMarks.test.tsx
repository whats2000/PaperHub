import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_MARK_PARAGRAPHS, useHtmlMarks } from "@/components/canvas/useHtmlMarks";
import { chunkMap, type ChunkMap } from "@/lib/marks/anchor";
import { useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { MarkCreated, MarkIn } from "@/types/notes";

const { toastFn, toastError, toastMessage } = vi.hoisted(() => ({
  toastFn: vi.fn(),
  toastError: vi.fn(),
  toastMessage: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: toastError, message: toastMessage, success: vi.fn() }),
}));

const PAPER = `
  <h2>1 Introduction</h2>
  <p>Title words before any chunk.</p>
  <p><span id="phchunk-0"></span>Robots learn from demonstrations.</p>
  <h2>2 Method</h2>
  <p><span id="phchunk-1"></span>We predict the agent pose ahead of time.</p>
`;

const CHUNKS: ChunkMap = chunkMap([
  { id: 101, dom_id: "phchunk-0", section: "1 Introduction" },
  { id: 102, dom_id: "phchunk-1", section: "2 Method" },
]);

function frameDoc(body: string): Document {
  const frame = document.createElement("iframe");
  document.body.appendChild(frame);
  const doc = frame.contentDocument!;
  doc.body.innerHTML = body;
  const FrameRange = (frame.contentWindow as unknown as { Range: typeof Range }).Range;
  FrameRange.prototype.getClientRects = () => [] as unknown as DOMRectList;
  FrameRange.prototype.getBoundingClientRect = () => new DOMRect(10, 40, 100, 16);
  return doc;
}

function Host({ doc }: { doc: Document }) {
  return <div>{useHtmlMarks({ paperId: 3, doc, isDark: false })}</div>;
}

/** Selects from the start of `from` to the end of `to` in `doc`. */
function select(doc: Document, from: string, to: string, how: "mouse" | "keyboard" = "mouse") {
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text);
  const start = nodes.find((n) => n.data.includes(from))!;
  const end = nodes.find((n) => n.data.includes(to) && (n === start || start.compareDocumentPosition(n) & 4))!;
  const range = doc.createRange();
  range.setStart(start, start.data.indexOf(from));
  range.setEnd(end, end.data.indexOf(to) + to.length);
  const sel = doc.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  act(() => {
    if (how === "mouse") doc.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    else doc.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowRight", shiftKey: true, bubbles: true }));
  });
}

function created(section: string, block: string): MarkCreated {
  return {
    mark: {
      id: `mk_${block}`,
      paper_content_id: 3,
      block_id: block,
      kind: "highlight",
      color: "green",
      view: "html",
      pieces: [],
      lang: null,
      source_ref: null,
      section_key: section,
      status: "ok",
      created_at: "2026-11-17T00:00:00Z",
      deleted_at: null,
    },
    block: null,
    saved_to: { note_id: 7, paper_title: "Duo", section, part_index: 0, part_count: 1 },
  };
}

let create: ReturnType<typeof vi.fn<(body: MarkIn, opts?: { quiet?: boolean }) => Promise<MarkCreated>>>;
let ensureChunks: ReturnType<typeof vi.fn<(paperId: number) => Promise<ChunkMap>>>;

beforeEach(() => {
  toastFn.mockReset();
  toastError.mockReset();
  toastMessage.mockReset();
  create = vi.fn((body: MarkIn) => Promise.resolve(created(body.section_key ?? "", `b${body.section_key}`)));
  ensureChunks = vi.fn(() => Promise.resolve(CHUNKS));
  useMarksStore.setState({
    marksByPaper: {},
    chunksByPaper: {},
    colors: [],
    showMarks: true,
    loadPaper: vi.fn(() => Promise.resolve()),
    loadColors: vi.fn(() => Promise.resolve()),
    ensureChunks,
    create,
  });
  useNotesStore.setState({ caretAfter: null, byTarget: {}, quoteTarget: null });
});

afterEach(() => {
  document.querySelectorAll("iframe").forEach((f) => f.remove());
});

describe("useHtmlMarks", () => {
  it("opens the popover under a selection and saves a highlight with its anchor", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    select(doc, "agent pose", "of time");
    const bar = await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      paper_content_id: 3,
      kind: "highlight",
      color: "green",
      section_key: "2 Method",
      pieces: [{ quote: "agent pose ahead of time", chunk_start: 102, chunk_end: 102 }],
    });
    expect(create.mock.calls[0]![1]).toEqual({ quiet: true });
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith("Saved to Duo › §2", expect.anything()));
    expect(doc.getSelection()!.isCollapsed).toBe(true);
    await waitFor(() => expect(bar.dataset.state).toBe("closed"));
  });

  it("saves a selection across two sections once per section, one toast; a retry skips what was saved", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    create.mockImplementationOnce((body) => Promise.resolve(created(body.section_key ?? "", "b1")));
    create.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    select(doc, "Robots", "agent pose");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Quote to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(toastFn).not.toHaveBeenCalled();
    // The selection stays; trying again saves only the missing section.
    fireEvent.click(screen.getByRole("button", { name: "Quote to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[2]![0].section_key).toBe("2 Method");
    await waitFor(() => expect(toastFn).toHaveBeenCalledTimes(1));
    expect(toastFn.mock.calls[0]![0]).toBe("Saved to Duo › §1, §2");
  });

  it("a retry over a new selection of the same text saves no section twice", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    create.mockImplementationOnce((body) => Promise.resolve(created(body.section_key ?? "", "b1")));
    create.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    select(doc, "Robots", "agent pose");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Quote to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    // A key press re-reads the selection: a new Range over the same text.
    select(doc, "Robots", "agent pose", "keyboard");
    await act(() => new Promise((r) => setTimeout(r, 10))); // the re-read settles
    fireEvent.click(screen.getByRole("button", { name: "Quote to note" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls.map((c) => c[0].section_key)).toEqual(["1 Introduction", "2 Method", "2 Method"]);
    await waitFor(() => expect(toastFn).toHaveBeenCalledTimes(1));
    expect(toastFn.mock.calls[0]![0]).toBe("Saved to Duo › §1, §2");
  });

  it("waits for the chunk anchors, and never saves a mark without them", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    ensureChunks.mockRejectedValueOnce(new Error("503"));
    select(doc, "agent pose", "of time");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(create).not.toHaveBeenCalled();
    // Loaded on the next try.
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(ensureChunks).toHaveBeenCalledTimes(2);
  });

  it("refuses a passage that no chunk holds", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    select(doc, "Title words", "any chunk");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Quote to note" }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("This passage isn't tied to the paper's text, so it can't be marked."),
    );
    expect(create).not.toHaveBeenCalled();
  });

  it("Add note opens the notes pane with the caret after the new reference", async () => {
    const doc = frameDoc(PAPER);
    const openAt = vi.spyOn(useNotesPaneStore.getState(), "openAt").mockImplementation(() => undefined);
    render(<Host doc={doc} />);
    select(doc, "agent pose", "of time");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() => expect(openAt).toHaveBeenCalled());
    expect(create.mock.calls[0]![0]).toMatchObject({ kind: "quote", color: null });
    expect(useNotesStore.getState().caretAfter).toEqual({ noteId: 7, blockId: "b2 Method" });
    expect(openAt).toHaveBeenCalledWith({ kind: "paper", id: 3, noteId: 7, blockId: "b2 Method" });
    openAt.mockRestore();
  });

  it("caps a select-all, naming the limit", async () => {
    const many = Array.from({ length: MAX_MARK_PARAGRAPHS + 1 }, (_, i) => `<p>Paragraph number ${i}.</p>`).join("");
    const doc = frameDoc(`<p><span id="phchunk-0"></span>Start.</p>${many}`);
    render(<Host doc={doc} />);
    select(doc, "Start", `number ${MAX_MARK_PARAGRAPHS}.`);
    await waitFor(() => expect(toastMessage).toHaveBeenCalledTimes(1));
    expect(toastMessage.mock.calls[0]![0]).toContain(String(MAX_MARK_PARAGRAPHS));
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("from the keyboard, focus moves into the popover; Escape gives it back", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    select(doc, "agent pose", "of time", "keyboard");
    const first = await screen.findByRole("button", { name: "Highlight: Key result" });
    await waitFor(() => expect(first).toHaveFocus());
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByRole("button", { name: "Highlight: Method" })).toHaveFocus();
    const focus = vi.spyOn(doc.defaultView!, "focus");
    fireEvent.keyDown(document.activeElement!, { key: "Escape", bubbles: true });
    expect(focus).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("toolbar").dataset.state).toBe("closed"));
  });
});
