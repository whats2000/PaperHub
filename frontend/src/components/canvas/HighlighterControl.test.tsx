import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BoxToolButton } from "@/components/canvas/BoxTool";
import { HighlighterControl } from "@/components/canvas/HighlighterControl";
import { useHtmlMarks } from "@/components/canvas/useHtmlMarks";
import { chunkMap, type ChunkMap } from "@/lib/marks/anchor";
import { useMarksStore } from "@/store/marks";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkCreated, MarkIn } from "@/types/notes";

const { toastFn, toastError, toastMessage } = vi.hoisted(() => ({
  toastFn: vi.fn(),
  toastError: vi.fn(),
  toastMessage: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: toastError, message: toastMessage, success: vi.fn() }),
}));

const PAPER = `
  <h2>2 Method</h2>
  <p id="p1"><span id="phchunk-1"></span>We predict the agent pose ahead of time.</p>
  <p id="p2"><span id="phchunk-2"></span>The latency drops by 89% on all trajectories.</p>
`;

const CHUNKS: ChunkMap = chunkMap([
  { id: 102, dom_id: "phchunk-1", section: "2 Method" },
  { id: 103, dom_id: "phchunk-2", section: "2 Method" },
]);

/** A paper in an iframe; each range has one line box, placed by its
 * paragraph and offsets (jsdom has no layout). */
function frameDoc(body: string): Document {
  const frame = document.createElement("iframe");
  document.body.appendChild(frame);
  const doc = frame.contentDocument!;
  doc.body.innerHTML = body;
  const FrameRange = (frame.contentWindow as unknown as { Range: typeof Range }).Range;
  FrameRange.prototype.getClientRects = function (this: Range) {
    const p = this.startContainer.parentElement?.closest("p");
    const top = p?.id === "p2" ? 60 : 20;
    return [new DOMRect(this.startOffset * 6, top, (this.endOffset - this.startOffset) * 6, 16)] as unknown as DOMRectList;
  };
  FrameRange.prototype.getBoundingClientRect = () => new DOMRect(10, 40, 100, 16);
  return doc;
}

function Reader({ doc }: { doc: Document }) {
  return (
    <>
      <HighlighterControl shown />
      <div>{useHtmlMarks({ paperId: 3, doc, isDark: false })}</div>
    </>
  );
}

/** Drags over `text` in `doc` (selects it, then the mouse comes up). */
function stroke(doc: Document, text: string) {
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  let node: Text | null = null;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if ((n as Text).data.includes(text)) {
      node = n as Text;
      break;
    }
  }
  const range = doc.createRange();
  range.setStart(node!, node!.data.indexOf(text));
  range.setEnd(node!, node!.data.indexOf(text) + text.length);
  const sel = doc.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  act(() => {
    doc.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
}

function mark(id: string, over: Partial<Mark> = {}): Mark {
  return {
    id,
    paper_content_id: 3,
    block_id: `b_${id}`,
    kind: "highlight",
    color: "green",
    view: "html",
    pieces: [],
    lang: null,
    source_ref: null,
    section_key: "2 Method",
    status: "ok",
    created_at: "2026-11-17T00:00:00Z",
    deleted_at: null,
    note_id: 7,
    ...over,
  };
}

let created = 0;
let create: ReturnType<typeof vi.fn<(body: MarkIn, opts?: { quiet?: boolean }) => Promise<MarkCreated>>>;
let split: ReturnType<typeof vi.fn<(id: string) => Promise<void>>>;
let erase: ReturnType<typeof vi.fn<(id: string, index: number) => Promise<void>>>;

beforeEach(() => {
  toastFn.mockReset();
  toastError.mockReset();
  toastMessage.mockReset();
  created = 0;
  // The server: a new mark, or the joined one with the piece appended.
  create = vi.fn((body: MarkIn) => {
    const list = useMarksStore.getState().marksByPaper[3] ?? [];
    const joined = body.join_to ? list.find((m) => m.id === body.join_to) : undefined;
    const m = joined
      ? { ...joined, pieces: [...joined.pieces, ...body.pieces] }
      : mark(`mk_${++created}`, { color: body.color, kind: body.kind, pieces: body.pieces });
    useMarksStore.setState((s) => ({
      marksByPaper: { ...s.marksByPaper, 3: [...(s.marksByPaper[3] ?? []).filter((x) => x.id !== m.id), m] },
    }));
    return Promise.resolve({
      mark: m,
      block: null,
      saved_to: { note_id: 7, paper_title: "Duo", section: body.section_key, part_index: 0, part_count: 1 },
    });
  });
  split = vi.fn(() => Promise.resolve());
  erase = vi.fn(() => Promise.resolve());
  useMarksStore.setState({
    marksByPaper: {},
    chunksByPaper: { 3: CHUNKS },
    colors: [],
    showMarks: true,
    boxOn: false,
    penOn: false,
    penColor: "green",
    penSession: [],
    loadPaper: vi.fn(() => Promise.resolve()),
    loadColors: vi.fn(() => Promise.resolve()),
    ensureChunks: vi.fn(() => Promise.resolve(CHUNKS)),
    create,
    split,
    erase,
  });
  useNotesStore.setState({ caretAfter: null, byTarget: {}, quoteTarget: null });
});

afterEach(() => {
  document.querySelectorAll("iframe").forEach((f) => f.remove());
});

describe("HighlighterControl", () => {
  it("turns the pen on with its colour named; the colours open beside it; Escape turns it off", () => {
    render(<HighlighterControl shown />);
    const button = screen.getByRole("button", { name: "Highlighter" });
    const group = document.querySelector<HTMLElement>('[role="group"][aria-label="Highlighter colour"]')!;
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(group.dataset.state).toBe("closed");
    fireEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.textContent).toBe("Highlighter: Key result");
    expect(group.dataset.state).toBe("open");
    // A colour picked is the pen's colour, and is remembered.
    fireEvent.click(screen.getByRole("button", { name: "Method" }));
    expect(button.textContent).toBe("Highlighter: Method");
    expect(useMarksStore.getState().penColor).toBe("blue");
    // An Escape something else took leaves the pen on.
    const handled = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    handled.preventDefault();
    act(() => {
      document.body.dispatchEvent(handled);
    });
    expect(useMarksStore.getState().penOn).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(useMarksStore.getState().penOn).toBe(false);
    expect(group.dataset.state).toBe("closed");
    // On again: the colour chosen last.
    fireEvent.click(button);
    expect(button.textContent).toBe("Highlighter: Method");
  });

  it("pen and Box turn each other off", () => {
    render(
      <>
        <BoxToolButton shown />
        <HighlighterControl shown />
      </>,
    );
    const pen = screen.getByRole("button", { name: "Highlighter" });
    const box = screen.getByRole("button", { name: /Box/ });
    fireEvent.click(box);
    fireEvent.click(pen);
    expect(box.getAttribute("aria-pressed")).toBe("false");
    expect(pen.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(box);
    expect(box.getAttribute("aria-pressed")).toBe("true");
    expect(pen.getAttribute("aria-pressed")).toBe("false");
  });
});

describe("the highlighter in the HTML reader", () => {
  it("marks a drag at once in the pen colour, with no popover", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "pink"));
    stroke(doc, "agent pose");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "highlight",
      color: "pink",
      section_key: "2 Method",
      pieces: [{ quote: "agent pose", chunk_start: 102 }],
    });
    expect(create.mock.calls[0]![0].join_to).toBeUndefined();
    expect(screen.queryByRole("toolbar")).toBeNull();
    await waitFor(() => expect(doc.getSelection()!.isCollapsed).toBe(true));
  });

  it("joins a stroke in the same paragraph and colour; Split makes the newest piece its own mark", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke(doc, "We predict");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    stroke(doc, "ahead of time");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]![0]).toMatchObject({ join_to: "mk_1", pieces: [{ quote: "ahead of time" }] });
    expect(useMarksStore.getState().marksByPaper[3]![0]!.pieces).toHaveLength(2);
    await waitFor(() => expect(toastFn).toHaveBeenLastCalledWith("Joined to the same mark", expect.anything()));
    const action = (toastFn.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    expect(action.label).toBe("Split");
    action.onClick();
    expect(split).toHaveBeenCalledWith("mk_1");
  });

  it("never joins across paragraphs, colours, or an earlier pen session", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke(doc, "We predict");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    // Another paragraph.
    stroke(doc, "latency drops");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]![0].join_to).toBeUndefined();
    // Another colour, same paragraph.
    act(() => useMarksStore.getState().setPen(true, "blue"));
    stroke(doc, "agent pose");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[2]![0].join_to).toBeUndefined();
    // The pen off and on again: a new session; older marks don't join.
    act(() => useMarksStore.getState().setPen(false));
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke(doc, "ahead of time");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(4));
    expect(create.mock.calls[3]![0].join_to).toBeUndefined();
  });

  it("a stroke across paragraphs files a piece per paragraph under its section", async () => {
    const doc = frameDoc(`
      <h2>1 Intro</h2>
      <p><span id="phchunk-1"></span>We predict the agent pose ahead of time.</p>
      <h2>2 Method</h2>
      <p><span id="phchunk-2"></span>The latency drops by 89% on all trajectories.</p>
    `);
    useMarksStore.setState({
      ensureChunks: vi.fn(() =>
        Promise.resolve(
          chunkMap([
            { id: 102, dom_id: "phchunk-1", section: "1 Intro" },
            { id: 103, dom_id: "phchunk-2", section: "2 Method" },
          ]),
        ),
      ),
    });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    const [a, b] = Array.from(doc.querySelectorAll("p")).map((p) => p.lastChild as Text);
    const range = doc.createRange();
    range.setStart(a!, 3);
    range.setEnd(b!, 11);
    doc.getSelection()!.addRange(range);
    act(() => {
      doc.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls.map((c) => c[0].section_key)).toEqual(["1 Intro", "2 Method"]);
    expect(create.mock.calls[0]![0].pieces).toHaveLength(1);
  });

  it("a pen click on a mark erases the piece under it", async () => {
    const doc = frameDoc(PAPER);
    useMarksStore.setState({
      marksByPaper: {
        3: [
          mark("mk_9", {
            pieces: [
              { quote: "We predict", chunk_start: 102, chunk_end: 102, prefix: "", suffix: "" },
              { quote: "ahead of time", chunk_start: 102, chunk_end: 102, prefix: "", suffix: "" },
            ],
          }),
        ],
      },
    });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    await waitFor(() => expect(doc.querySelectorAll('.ph-mk[data-mark="mk_9"]')).toHaveLength(2));
    const x = "We predict the agent pose ".length * 6 + 3;
    act(() => {
      doc.body.dispatchEvent(new MouseEvent("click", { clientX: x, clientY: 25, bubbles: true }));
    });
    expect(erase).toHaveBeenCalledWith("mk_9", 1);
  });

  it("a pen click on a figure marks it whole in the pen colour, and opens no lightbox", async () => {
    const doc = frameDoc(`${PAPER}<span id="phfig-001"></span><figure><img src="a.png"><figcaption>Figure 1: Robot arm.</figcaption></figure>`);
    const lightbox = vi.fn();
    doc.body.addEventListener("click", lightbox);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "purple"));
    act(() => {
      doc.querySelector("img")!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "figure",
      color: "purple",
      pieces: [{ quote: "Figure 1: Robot arm.", figure_id: "fig-001" }],
    });
    expect(lightbox).not.toHaveBeenCalled();
  });

  it("with the pen off, a selection opens the popover again; Escape in the paper turns the pen off", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    expect(doc.documentElement.getAttribute("data-ph-pen")).toBe("green");
    act(() => {
      doc.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(useMarksStore.getState().penOn).toBe(false);
    expect(doc.documentElement.hasAttribute("data-ph-pen")).toBe(false);
    stroke(doc, "agent pose");
    expect(await screen.findByRole("toolbar", { name: "Mark the selection" })).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });
});
