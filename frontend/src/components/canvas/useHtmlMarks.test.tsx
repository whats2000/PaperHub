import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useHtmlMarks } from "@/components/canvas/useHtmlMarks";
import { MAX_MARK_PARAGRAPHS } from "@/lib/marks/limits";
import { chunkMap, type ChunkMap } from "@/lib/marks/anchor";
import { useCanvasStore } from "@/store/canvas";
import { useMarkNavStore } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useTranslationStore } from "@/store/translation";
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
      kind: "text", style: "highlight",
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

const realOpenAt = useNotesPaneStore.getState().openAt;

beforeEach(() => {
  useMarkNavStore.setState({ card: null });
  useNotesStore.setState({ caretAfter: null });
  useNotesPaneStore.setState({ open: false, openAt: realOpenAt });
  toastFn.mockReset();
  toastError.mockReset();
  toastMessage.mockReset();
  create = vi.fn((body: MarkIn) => Promise.resolve(created(body.section_key ?? "", `b${body.section_key}`)));
  ensureChunks = vi.fn(() => Promise.resolve(CHUNKS));
  useMarksStore.setState({
    markStyle: "highlight",
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
      kind: "text", style: "highlight",
      color: "green",
      section_key: "2 Method",
      pieces: [{ quote: "agent pose ahead of time", chunk_start: 102, chunk_end: 102 }],
    });
    expect(create.mock.calls[0]![1]).toEqual({ quiet: true });
    // No toast on a normal save: the note card opens beside the mark, the
    // caret on a new line under its quote.
    await waitFor(() => expect(useMarkNavStore.getState().card).toMatchObject({ paperId: 3, markId: "mk_b2 Method" }));
    expect(useNotesStore.getState().caretAfter).toMatchObject({ noteId: 7, blockId: "b2 Method", in: "card" });
    expect(toastFn).not.toHaveBeenCalled();
    expect(doc.getSelection()!.isCollapsed).toBe(true);
    await waitFor(() => expect(bar.dataset.state).toBe("closed"));
  });

  it("with the notes pane beside the paper, no card: the pane shows the new block with the caret under it", async () => {
    const openAt = vi.fn();
    useNotesPaneStore.setState({ open: true, openAt });
    useCanvasStore.setState({ open: true, width: 900 });
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    select(doc, "agent pose", "of time");
    await screen.findByRole("toolbar", { name: "Mark the selection" });
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(openAt).toHaveBeenCalledWith({ kind: "paper", id: 3, noteId: 7, blockId: "b2 Method" }));
    expect(useNotesStore.getState().caretAfter).toEqual({ noteId: 7, blockId: "b2 Method" });
    expect(useMarkNavStore.getState().card).toBeNull();
    expect(toastFn).not.toHaveBeenCalled();
  });

  it("saves a selection across two sections once per section, one toast; a retry skips what was saved", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    create.mockImplementationOnce((body) => Promise.resolve(created(body.section_key ?? "", "b1")));
    create.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    select(doc, "Robots", "agent pose");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(toastFn).not.toHaveBeenCalled();
    // The selection stays; trying again saves only the missing section.
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[2]![0].section_key).toBe("2 Method");
    await waitFor(() => expect(useMarkNavStore.getState().card).not.toBeNull());
    expect(toastFn).not.toHaveBeenCalled();
  });

  it("a retry over a new selection of the same text saves no section twice", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    create.mockImplementationOnce((body) => Promise.resolve(created(body.section_key ?? "", "b1")));
    create.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    select(doc, "Robots", "agent pose");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    // A key press re-reads the selection: a new Range over the same text.
    select(doc, "Robots", "agent pose", "keyboard");
    await act(() => new Promise((r) => setTimeout(r, 10))); // the re-read settles
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls.map((c) => c[0].section_key)).toEqual(["1 Introduction", "2 Method", "2 Method"]);
    await waitFor(() => expect(useMarkNavStore.getState().card).not.toBeNull());
    expect(toastFn).not.toHaveBeenCalled();
  });

  it("a retry with another action starts fresh: every section saves with the new kind", async () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    create.mockImplementationOnce((body) => Promise.resolve(created(body.section_key ?? "", "b1")));
    create.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    select(doc, "Robots", "agent pose");
    await screen.findByRole("toolbar");
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Underline" }));
    fireEvent.click(screen.getByRole("button", { name: "Underline: no colour" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(4));
    expect(create.mock.calls.slice(2).map((c) => [c[0].kind, c[0].style, c[0].color, c[0].section_key])).toEqual([
      ["text", "underline", null, "1 Introduction"],
      ["text", "underline", null, "2 Method"],
    ]);
    await waitFor(() => expect(useMarkNavStore.getState().card).not.toBeNull());
    expect(toastFn).not.toHaveBeenCalled();
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
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Key result" }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("This passage isn't tied to the paper's text, so it can't be marked."),
    );
    expect(create).not.toHaveBeenCalled();
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
    const first = await screen.findByRole("button", { name: "Highlight" });
    await waitFor(() => expect(first).toHaveFocus());
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByRole("button", { name: "Underline" })).toHaveFocus();
    const focus = vi.spyOn(doc.defaultView!, "focus");
    fireEvent.keyDown(document.activeElement!, { key: "Escape", bubbles: true });
    expect(focus).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("toolbar").dataset.state).toBe("closed"));
  });
});

describe("the reader and the note card", () => {
  beforeEach(() => {
    useMarkNavStore.setState({ card: null, hovered: null });
    useCanvasStore.setState({ readerView: null, readerTabRequest: null });
  });

  it("closes the note card on Escape or a click in the paper", () => {
    const doc = frameDoc(PAPER);
    render(<Host doc={doc} />);
    act(() => useMarkNavStore.getState().openCard(3, "mk_1"));
    act(() => {
      doc.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(useMarkNavStore.getState().card).toBeNull();
    act(() => useMarkNavStore.getState().openCard(3, "mk_1"));
    act(() => {
      doc.body.querySelector("p")!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(useMarkNavStore.getState().card).toBeNull();
  });

  it("tries the PDF view for a mark it can't find in the HTML", async () => {
    const doc = frameDoc(PAPER);
    useMarksStore.setState({
      marksByPaper: {
        3: [
          {
            id: "mk_pdf",
            paper_content_id: 3,
            block_id: "b1",
            kind: "text", style: "highlight",
            color: "green",
            view: "pdf",
            pieces: [{ quote: "words drawn only on a page", page: 1 }],
            lang: null,
            source_ref: null,
            section_key: "2 Method",
            status: "ok",
            created_at: "2026-11-17T00:00:00Z",
            deleted_at: null,
          },
        ],
      },
    });
    render(<Host doc={doc} />);
    act(() => useTranslationStore.getState().requestReveal(3, "mk_pdf", { fallback: true }));
    await waitFor(() => expect(useCanvasStore.getState().readerTabRequest).toMatchObject({ paperId: 3, tab: "pdf" }), {
      timeout: 3000,
    });
    // Asked again, with no further fallback.
    expect(useTranslationStore.getState().reveal).toMatchObject({ paperId: 3, markId: "mk_pdf" });
    expect(useTranslationStore.getState().reveal?.fallback).toBeUndefined();
  });

  it("leaves a request to the PDF view while the PDF tab shows", () => {
    const doc = frameDoc(PAPER);
    useCanvasStore.setState({ readerView: { paperId: 3, kind: "pdf" } });
    render(<Host doc={doc} />);
    const claim = vi.spyOn(useTranslationStore.getState(), "claimReveal");
    act(() => useTranslationStore.getState().requestReveal(3, "mk_x", { fallback: true }));
    expect(claim).not.toHaveBeenCalled();
    claim.mockRestore();
  });
});
