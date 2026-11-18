import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HighlighterControl } from "@/components/canvas/HighlighterControl";
import { useHtmlMarks } from "@/components/canvas/useHtmlMarks";
import { usePenSave, type PenPiece } from "@/components/canvas/useMarkSave";
import { ApiError, erasePiece } from "@/lib/api";
import { chunkMap, type ChunkMap } from "@/lib/marks/anchor";
import { useMarkNavStore } from "@/store/markNav";
import { resetMarkOps, useMarksStore } from "@/store/marks";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkCreated, MarkIn } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), erasePiece: vi.fn() }));

/** The store's own erase (the tests' default stands in a mock). */
const realErase = useMarksStore.getState().erase;

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
    kind: "text", style: "highlight",
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
  resetMarkOps();
  vi.mocked(erasePiece).mockReset();
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

describe("the highlighter in the HTML reader", () => {
  it("marks a drag at once in the pen colour, with no popover", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "pink"));
    stroke(doc, "agent pose");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]![0]).toMatchObject({
      kind: "text", style: "highlight",
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

/** Lets a mouseup's read (a timer) run. */
const tick = () => act(() => new Promise((r) => setTimeout(r, 0)));

/** A `create` whose answers the test gives, one by one. */
function heldCreates() {
  const pending: (() => Promise<void>)[] = [];
  const inner = create.getMockImplementation()!;
  create.mockImplementation(
    (body: MarkIn, opts?: { quiet?: boolean }) =>
      new Promise<MarkCreated>((resolve, reject) => {
        pending.push(() => inner(body, opts).then(resolve, reject));
      }),
  );
  return async () => {
    await waitFor(() => expect(pending.length).toBeGreaterThan(0));
    await act(() => pending.shift()!());
  };
}

describe("strokes, erases and splits keep the user's order", () => {
  it("fast strokes, each before the last has saved, join in order", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    const answer = heldCreates();
    stroke(doc, "We predict");
    await tick();
    stroke(doc, "agent pose");
    await tick();
    stroke(doc, "ahead of time");
    await tick();
    // One at a time: the second waits for the first.
    expect(create).toHaveBeenCalledTimes(1);
    await answer();
    await answer();
    await answer();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls.map((c) => [c[0].pieces[0]!.quote, c[0].join_to])).toEqual([
      ["We predict", undefined],
      ["agent pose", "mk_1"],
      ["ahead of time", "mk_1"],
    ]);
    expect(useMarksStore.getState().marksByPaper[3]![0]!.pieces).toHaveLength(3);
  });

  it("a pen click while a join is saving erases after it: the joined piece is kept", async () => {
    const doc = frameDoc(PAPER);
    useMarksStore.setState({ erase: realErase });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke(doc, "We predict");
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]).toHaveLength(1));
    await waitFor(() => expect(doc.querySelectorAll('.ph-mk[data-mark="mk_1"]')).toHaveLength(1));
    const answer = heldCreates();
    vi.mocked(erasePiece).mockImplementation((id, index) => {
      const m = useMarksStore.getState().marksByPaper[3]!.find((x) => x.id === id)!;
      return Promise.resolve({ ...m, pieces: m.pieces.map((p, i) => (i === index ? { ...p, erased: true } : p)) });
    });
    stroke(doc, "ahead of time");
    await tick();
    // The first piece, clicked before the join has saved (the press
    // collapses the stroke's selection first).
    act(() => {
      doc.getSelection()!.removeAllRanges();
      doc.body.dispatchEvent(new MouseEvent("click", { clientX: 3, clientY: 25, bubbles: true }));
    });
    await tick();
    expect(erasePiece).not.toHaveBeenCalled();
    await answer();
    await waitFor(() => expect(erasePiece).toHaveBeenCalledWith("mk_1", 0, expect.any(AbortSignal)));
    // The mark was not deleted: the joined piece is still on it.
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]![0]!.pieces[0]!.erased).toBe(true));
    const m = useMarksStore.getState().marksByPaper[3]![0]!;
    expect(m.deleted_at).toBeNull();
    expect(m.pieces.map((p) => [p.quote, !!p.erased])).toEqual([
      ["We predict", true],
      ["ahead of time", false],
    ]);
  });

  it("a join the server refuses (the mark was deleted meanwhile) becomes a mark of its own", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke(doc, "We predict");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const inner = create.getMockImplementation()!;
    create.mockImplementation((body, opts) =>
      body.join_to ? Promise.reject(new ApiError(422, "cannot join a deleted mark")) : inner(body, opts),
    );
    stroke(doc, "ahead of time");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[1]![0].join_to).toBe("mk_1");
    expect(create.mock.calls[2]![0].join_to).toBeUndefined();
    expect(create.mock.calls[2]![0].pieces[0]!.quote).toBe("ahead of time");
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]).toHaveLength(2));
    // No toast; the newest mark's note card opens, as from the menu.
    expect(toastFn).not.toHaveBeenCalled();
    await waitFor(() => expect(useMarkNavStore.getState().card).toMatchObject({ markId: "mk_2" }));
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe("a stroke that partly fails", () => {
  const TWO_SECTIONS = `
    <h2>1 Intro</h2>
    <p><span id="phchunk-1"></span>We predict the agent pose ahead of time.</p>
    <h2>2 Method</h2>
    <p><span id="phchunk-2"></span>The latency drops by 89% on all trajectories.</p>
  `;
  const MAP = chunkMap([
    { id: 102, dom_id: "phchunk-1", section: "1 Intro" },
    { id: 103, dom_id: "phchunk-2", section: "2 Method" },
  ]);

  function strokeAcross(doc: Document) {
    const [a, b] = Array.from(doc.querySelectorAll("p")).map((p) => p.lastChild as Text);
    const range = doc.createRange();
    range.setStart(a!, 3);
    range.setEnd(b!, 11);
    const sel = doc.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    act(() => {
      doc.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
  }

  function failSecondOnce() {
    const inner = create.getMockImplementation()!;
    let failed = false;
    create.mockImplementation((body, opts) => {
      if (body.section_key === "2 Method" && !failed) {
        failed = true;
        return Promise.reject(new ApiError(500, "boom"));
      }
      return inner(body, opts);
    });
  }

  it("says so with Retry, which saves only the section still missing", async () => {
    const doc = frameDoc(TWO_SECTIONS);
    useMarksStore.setState({ ensureChunks: vi.fn(() => Promise.resolve(MAP)) });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    failSecondOnce();
    strokeAcross(doc);
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Couldn't save the mark.", expect.anything()));
    // What saved is in the pen session already.
    expect(useMarksStore.getState().penSession.map((m) => m.id)).toEqual(["mk_1"]);
    const retry = (toastError.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    expect(retry.label).toBe("Retry");
    act(() => retry.onClick());
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls.map((c) => c[0].section_key)).toEqual(["1 Intro", "2 Method", "2 Method"]);
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]).toHaveLength(2));
    expect(toastFn).not.toHaveBeenCalled();
  });

  it("after the pen session ends, neither a re-stroke nor the old Retry marks the saved text twice", async () => {
    const doc = frameDoc(TWO_SECTIONS);
    useMarksStore.setState({ ensureChunks: vi.fn(() => Promise.resolve(MAP)) });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    failSecondOnce();
    strokeAcross(doc);
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    const retry = (toastError.mock.lastCall![1] as { action: { onClick: () => void } }).action;
    // A new session: the failed stroke's progress is forgotten.
    act(() => useMarksStore.getState().setPen(false));
    act(() => useMarksStore.getState().setPen(true, "green"));
    strokeAcross(doc);
    // §1 is already marked (the first attempt saved it): only §2 is new.
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls[2]![0].section_key).toBe("2 Method");
    // The old Retry finds both saved now: no duplicate.
    act(() => retry.onClick());
    await waitFor(() => expect(toastFn.mock.lastCall?.[0]).toBe("Already saved"));
    expect(create).toHaveBeenCalledTimes(3);
  });

  it("pen off/on forgets a failed stroke's progress: a mark deleted since is made again", async () => {
    const doc = frameDoc(TWO_SECTIONS);
    useMarksStore.setState({ ensureChunks: vi.fn(() => Promise.resolve(MAP)) });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    failSecondOnce();
    strokeAcross(doc);
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    // §1's mark is deleted (elsewhere) before the stroke is redone: the
    // server has nothing to match it, so only the progress record could
    // skip it.
    act(() => {
      useMarksStore.setState({ marksByPaper: { 3: [] } });
    });
    act(() => useMarksStore.getState().setPen(false));
    act(() => useMarksStore.getState().setPen(true, "green"));
    strokeAcross(doc);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(4));
    expect(create.mock.calls.slice(2).map((c) => c[0].section_key)).toEqual(["1 Intro", "2 Method"]);
  });

  it("a retried join says Joined; a retry that only finishes a stroke says Saved", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke(doc, "We predict");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const inner = create.getMockImplementation()!;
    let failed = false;
    create.mockImplementation((body, opts) => {
      if (body.join_to && !failed) {
        failed = true;
        return Promise.reject(new ApiError(500, "boom"));
      }
      return inner(body, opts);
    });
    stroke(doc, "ahead of time");
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    const retry = (toastError.mock.lastCall![1] as { action: { onClick: () => void } }).action;
    act(() => retry.onClick());
    await waitFor(() => expect(toastFn).toHaveBeenLastCalledWith("Joined to the same mark", expect.anything()));
    expect(create.mock.calls[2]![0].join_to).toBe("mk_1");
  });

  it("stroking the same text again saves only what is missing too", async () => {
    const doc = frameDoc(TWO_SECTIONS);
    useMarksStore.setState({ ensureChunks: vi.fn(() => Promise.resolve(MAP)) });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    failSecondOnce();
    strokeAcross(doc);
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    strokeAcross(doc);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect(create.mock.calls.map((c) => c[0].section_key)).toEqual(["1 Intro", "2 Method", "2 Method"]);
  });
});

describe("Escape and the selection", () => {
  it("an Escape in the paper while a menu or dialog is open leaves the pen on", () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    act(() => {
      doc.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(useMarksStore.getState().penOn).toBe(true);
    dialog.remove();
  });

  it("turning the pen on clears a selection left in the paper (it would look marked)", () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    const text = doc.querySelector("p")!.lastChild as Text;
    const range = doc.createRange();
    range.setStart(text, 0);
    range.setEnd(text, 6);
    doc.getSelection()!.addRange(range);
    act(() => useMarksStore.getState().setPen(true, "green"));
    expect(doc.getSelection()!.isCollapsed).toBe(true);
  });
});

describe("a stroke whose request never answers", () => {
  it("times out with the error toast and Retry; the next stroke still saves", async () => {
    resetMarkOps(60);
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    const inner = create.getMockImplementation()!;
    let hang = true;
    create.mockImplementation((body, opts) => {
      if (hang) {
        hang = false;
        return new Promise<MarkCreated>(() => undefined);
      }
      return inner(body, opts);
    });
    stroke(doc, "We predict");
    await tick();
    stroke(doc, "latency drops");
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Couldn't save the mark.", expect.anything()));
    expect((toastError.mock.lastCall![1] as { action: { label: string } }).action.label).toBe("Retry");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]![0].pieces[0]!.quote).toBe("latency drops");
    expect(toastError).toHaveBeenCalledTimes(1);
  });
});

describe("a stroke that reached the server but timed out", () => {
  it("is reconciled from the server, and Retry adds no duplicate", async () => {
    resetMarkOps(60);
    const doc = frameDoc(PAPER);
    // The server's marks: what the refetch brings back.
    const server: Mark[] = [];
    const refetchMarks = vi.fn(() => {
      useMarksStore.setState((st) => ({ marksByPaper: { ...st.marksByPaper, 3: [...server] } }));
      return Promise.resolve();
    });
    useMarksStore.setState({ refetchMarks });
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    // The create lands on the server, but its answer never arrives.
    create.mockImplementation((body) => {
      server.push(mark("mk_s1", { color: body.color, pieces: body.pieces.map((p) => ({ ...p, stroke: 0 })) }));
      return new Promise<MarkCreated>(() => undefined);
    });
    stroke(doc, "agent pose");
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Couldn't save the mark.", expect.anything()));
    // Reconciled once: the store now holds the server's mark.
    await waitFor(() => expect(refetchMarks).toHaveBeenCalledWith(3, expect.any(AbortSignal)));
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]!.map((m) => m.id)).toEqual(["mk_s1"]));
    const retry = (toastError.mock.lastCall![1] as { action: { onClick: () => void } }).action;
    act(() => retry.onClick());
    await waitFor(() => expect(toastFn.mock.lastCall?.[0]).toBe("Already saved"));
    expect(create).toHaveBeenCalledTimes(1);
    expect(useMarksStore.getState().marksByPaper[3]).toHaveLength(1);
  });

  it("a piece the server lost is not held: re-stroking it marks it again", async () => {
    const doc = frameDoc(PAPER);
    render(<Reader doc={doc} />);
    act(() => useMarksStore.getState().setPen(true, "green"));
    stroke(doc, "agent pose");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    // After a re-ingest the server lost that piece.
    act(() => {
      useMarksStore.setState((st) => ({
        marksByPaper: { 3: st.marksByPaper[3]!.map((m) => ({ ...m, pieces: m.pieces.map((p) => ({ ...p, lost: true })) })) },
      }));
    });
    stroke(doc, "agent pose");
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(toastFn.mock.lastCall?.[0]).not.toBe("Already saved");
  });

  it("a PDF stroke whose place can't be found in time says so", async () => {
    resetMarkOps(60);
    act(() => useMarksStore.getState().setPen(true, "green"));
    const { result } = renderHook(() => usePenSave(3));
    act(() => result.current({ view: "pdf", pieces: () => new Promise<PenPiece[]>(() => undefined) }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "Couldn't find where this passage is in the paper in time.",
        expect.anything(),
      ),
    );
    expect(create).not.toHaveBeenCalled();
  });
});

describe("usePenSave across papers", () => {
  it("forgets a failed stroke's progress when the paper changes", async () => {
    const piece = (quote: string, section: string): PenPiece => ({ section, piece: { quote }, place: null });
    const placed = [piece("one", "1 Intro"), piece("two", "2 Method")];
    let failed = false;
    create.mockImplementation((body) => {
      if (body.section_key === "2 Method" && !failed) {
        failed = true;
        return Promise.reject(new ApiError(500, "boom"));
      }
      return Promise.resolve({
        mark: mark(`mk_${body.section_key}_${create.mock.calls.length}`, { pieces: body.pieces }),
        block: null,
        saved_to: { note_id: 7, paper_title: "Duo", section: body.section_key, part_index: 0, part_count: 1 },
      });
    });
    act(() => useMarksStore.getState().setPen(true, "green"));
    const { result, rerender } = renderHook(({ pid }) => usePenSave(pid), { initialProps: { pid: 3 } });
    act(() => result.current({ view: "html", pieces: () => placed }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    rerender({ pid: 4 });
    rerender({ pid: 3 });
    act(() => result.current({ view: "html", pieces: () => placed }));
    // Both sections again: what the earlier stroke saved was forgotten.
    await waitFor(() => expect(create).toHaveBeenCalledTimes(4));
    expect(create.mock.calls.slice(2).map((c) => c[0].section_key)).toEqual(["1 Intro", "2 Method"]);
  });
});

