import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NoteCard } from "@/components/canvas/NoteCard";
import { NoteEditor } from "@/components/notes/NoteEditor";
import { deleteMark, restoreMark } from "@/lib/api";
import { CANVAS_DEFAULT_WIDTH, useCanvasStore } from "@/store/canvas";
import { registerMarkAnchors, useMarkNavStore, type ClientRect } from "@/store/markNav";
import { resetMarkOps, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import { useTranslationStore } from "@/store/translation";
import type { Mark, NoteBlock } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listMarkColors: vi.fn(() =>
    Promise.resolve([
      { color: "green", name: "Key result", visible: true },
      { color: "blue", name: "Method", visible: true },
    ]),
  ),
  deleteMark: vi.fn(() => Promise.resolve({})),
  restoreMark: vi.fn(() => Promise.resolve({})),
}));
const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: vi.fn(), message: vi.fn(), success: vi.fn(), dismiss: vi.fn() }),
}));

function blk(id: string, extra: Partial<NoteBlock>): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: "paragraph",
    props: {},
    content: null,
    text: "",
    author: "user",
    section_key: "2 Method",
    changed_rev: 1,
    ...extra,
  };
}

function ref(id: string, markId: string, quote: string, color = "green"): NoteBlock {
  return blk(id, {
    type: "paperQuote",
    props: {
      markId,
      color,
      kind: "highlight",
      quote,
      pieces: [{ quote, page: 1 }],
      source: { page: 1, section: "2 Method" },
    },
    text: quote,
  });
}

function para(id: string, text: string): NoteBlock {
  return blk(id, { content: [{ type: "text", text, styles: {} }], text });
}

// §2 has three notes: the clicked mark (mk_1) is in the second, with
// another reference (mk_3) beside it.
const BLOCKS: NoteBlock[] = [
  blk("s1", { type: "section", props: { title: "2 Method" }, text: "2 Method" }),
  ref("q2", "mk_2", "the first note's quote"),
  para("p1", "First note words"),
  blk("d1", { type: "divider" }),
  ref("q1", "mk_1", "attention is all you need"),
  ref("q3", "mk_3", "a second passage", "blue"),
  para("p2", "My thoughts"),
  blk("d2", { type: "divider" }),
  para("p3", "Third note words"),
];

function mark(id: string, blockId: string, over: Partial<Mark> = {}): Mark {
  return {
    id,
    paper_content_id: 3,
    block_id: blockId,
    kind: "highlight",
    color: "green",
    view: "html",
    pieces: [{ quote: "q", chunk_start: 1, chunk_end: 1 }],
    lang: null,
    source_ref: null,
    section_key: "2 Method",
    status: "ok",
    created_at: "2026-11-16T00:00:00Z",
    deleted_at: null,
    note_id: 7,
    ...over,
  };
}

const rect = (top: number, left: number, width: number, height: number): ClientRect => ({
  top,
  left,
  width,
  height,
  bottom: top + height,
  right: left + width,
});

let unregister: (() => void) | null = null;
let markTop = 200;

function seed(marks: Mark[] = [mark("mk_1", "q1"), mark("mk_2", "q2"), mark("mk_3", "q3")]) {
  useMarksStore.setState({ marksByPaper: { 3: marks }, colors: [] });
  useNotesStore.setState({
    notes: {
      7: {
        note: {
          id: 7,
          target_kind: "paper",
          target_id: "3",
          title: "Attention Is All You Need",
          revision: 4,
          updated_at: "2026-11-16T00:00:00Z",
        },
        blocks: BLOCKS,
        suggestions: [],
        comments: [],
        revision: 4,
      },
    },
    byTarget: { "paper:3": 7 },
    reloads: {},
    colorNames: {},
    activePart: {},
    folded: {},
    focusOn: false,
    loadNote: vi.fn(() => Promise.resolve()),
  });
  // The reader draws the marks: the reader's area starts 800px in, so the
  // gutter left of it holds the card.
  unregister = registerMarkAnchors(3, {
    find: () => ({ rects: [rect(markTop, 820, 300, 18)], viewport: rect(40, 800, 600, 700) }),
  });
}

beforeEach(() => {
  toastFn.mockReset();
  resetMarkOps();
  markTop = 200;
  useMarkNavStore.setState({ card: null, hovered: null, back: null, cardBox: null });
  useNotesPaneStore.setState({ open: false, activeTab: "reader" });
  useCanvasStore.setState({ open: true, width: CANVAS_DEFAULT_WIDTH });
});
afterEach(() => {
  unregister?.();
  unregister = null;
  clearPendingNoteOps();
});

async function openCard(markId = "mk_1") {
  act(() => useMarkNavStore.getState().openCard(3, markId));
  render(<NoteCard />);
  return screen.findByRole("dialog", { name: "Note for this passage" });
}

const outer = (blockId: string) => document.querySelector<HTMLElement>(`.bn-block-outer[data-id="${blockId}"]`);

describe("the note card", () => {
  it("names the paper, the section and which note of it, in the gutter left of the reader", async () => {
    seed();
    const card = await openCard();
    expect(screen.getByTestId("note-card-title")).toHaveTextContent("Attention Is All You Need› §2 · note 2 of 3");
    // Beside the reader, never over it.
    expect(parseFloat(card.style.left) + parseFloat(card.style.width)).toBeLessThanOrEqual(800);
    expect(useMarkNavStore.getState().cardBox).not.toBeNull();
  });

  it("folds the section's other notes to one line each and shows the current one editable", async () => {
    seed();
    await openCard();
    expect(screen.getByTestId("note-card-part-s1")).toHaveTextContent("Note 1 · 2 blocks· the first note's quote");
    expect(screen.getByTestId("note-card-part-d2")).toHaveTextContent("Note 3 · 1 block· Third note words");
    await waitFor(() => expect(outer("q1")).not.toBeNull());
    // Only the current note shows; the rest of the note is hidden.
    await waitFor(() => expect(outer("q2")).toHaveAttribute("data-card-hidden", "true"));
    expect(outer("s1")).toHaveAttribute("data-card-hidden", "true");
    expect(outer("p3")).toHaveAttribute("data-card-hidden", "true");
    expect(outer("q1")).not.toHaveAttribute("data-card-hidden");
    expect(outer("p2")).not.toHaveAttribute("data-card-hidden");
    // Another note opens in its place.
    fireEvent.click(screen.getByTestId("note-card-part-s1"));
    await waitFor(() => expect(outer("q2")).not.toHaveAttribute("data-card-hidden"));
    expect(outer("q1")).toHaveAttribute("data-card-hidden", "true");
    expect(screen.getByTestId("note-card-title")).toHaveTextContent("note 1 of 3");
    expect(screen.getByTestId("note-card-part-d1")).toHaveTextContent("Note 2 · 3 blocks");
  });

  it("tints only the clicked reference; another reference is a Show jumper that re-points the card", async () => {
    seed();
    await openCard();
    const clicked = await screen.findByTestId("paper-ref-q1");
    const other = screen.getByTestId("paper-ref-q3");
    expect(clicked).toHaveAttribute("data-target", "true");
    expect(other).not.toHaveAttribute("data-target");
    expect(within(clicked).queryByRole("button", { name: /Show this passage/ })).toBeNull();
    // A reference in the card has no source chip: it is beside the paper.
    expect(within(clicked).queryByRole("button", { name: /Open in the paper/ })).toBeNull();
    const reveal = vi.spyOn(useTranslationStore.getState(), "requestReveal");
    fireEvent.click(within(other).getByRole("button", { name: /Show this passage in the paper/ }));
    await waitFor(() => expect(useMarkNavStore.getState().card?.markId).toBe("mk_3"));
    expect(reveal).toHaveBeenCalledWith(3, "mk_3", { fallback: true });
    await waitFor(() => expect(screen.getByTestId("paper-ref-q3")).toHaveAttribute("data-target", "true"));
    expect(screen.getByTestId("paper-ref-q1")).not.toHaveAttribute("data-target");
    reveal.mockRestore();
  });

  it("deletes the mark from its trash, with Undo that turns into Delete again", async () => {
    seed();
    await openCard();
    fireEvent.click(screen.getByRole("button", { name: "Delete this mark" }));
    expect(useMarkNavStore.getState().card).toBeNull();
    await waitFor(() => expect(deleteMark).toHaveBeenCalledWith("mk_1", expect.anything()));
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith("Mark deleted", expect.anything()));
    expect(useMarksStore.getState().marksByPaper[3]!.map((m) => m.id)).not.toContain("mk_1");
    const undo = (toastFn.mock.calls.at(-1)![1] as { action: { label: string; onClick: () => void } }).action;
    expect(undo.label).toBe("Undo");
    act(() => undo.onClick());
    await waitFor(() => expect(restoreMark).toHaveBeenCalledWith("mk_1", expect.anything()));
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith("Mark restored", expect.anything()));
    const again = (toastFn.mock.calls.at(-1)![1] as { action: { label: string } }).action;
    expect(again.label).toBe("Delete again");
    expect(useMarksStore.getState().marksByPaper[3]!.map((m) => m.id)).toContain("mk_1");
  });

  it("closes on Escape, on a click outside and from its close button, shrinking toward the mark", async () => {
    seed();
    const card = await openCard();
    // It grows from (and shrinks back to) its arrow at the mark.
    expect(card.style.transformOrigin).toMatch(/^right \d+(\.\d+)?px$/);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useMarkNavStore.getState().card).toBeNull();
    expect(card).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Note for this passage" })).toBeNull());

    act(() => useMarkNavStore.getState().openCard(3, "mk_1"));
    await screen.findByRole("dialog", { name: "Note for this passage" });
    fireEvent.pointerDown(document.body);
    fireEvent.click(document.body);
    expect(useMarkNavStore.getState().card).toBeNull();

    act(() => useMarkNavStore.getState().openCard(3, "mk_1"));
    const again = await screen.findByRole("dialog", { name: "Note for this passage" });
    // A click inside does not close it.
    fireEvent.pointerDown(again);
    fireEvent.click(again);
    expect(useMarkNavStore.getState().card).not.toBeNull();
    fireEvent.click(within(again).getByRole("button", { name: "Close" }));
    expect(useMarkNavStore.getState().card).toBeNull();
  });

  it("stays on screen when its mark scrolls away", async () => {
    seed();
    const card = await openCard();
    markTop = -900;
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });
    await waitFor(() => expect(parseFloat(card.style.top)).toBeGreaterThanOrEqual(40));
  });

  it("opens the note in the notes pane from Open in Notes", async () => {
    seed();
    const openAt = vi.fn();
    useNotesPaneStore.setState({ openAt });
    await openCard();
    fireEvent.click(screen.getByRole("button", { name: "Open in Notes" }));
    expect(openAt).toHaveBeenCalledWith({ kind: "paper", id: 3, noteId: 7, blockId: "q1" });
    expect(useMarkNavStore.getState().card).toBeNull();
  });

  it("says when a mark on a translation isn't showing, and switches to its language", async () => {
    seed([mark("mk_1", "q1", { lang: "繁體中文", view: "html" }), mark("mk_3", "q3")]);
    useTranslationStore.setState({ enabledByPaper: { 3: false }, language: "日本語" });
    const showMark = vi.spyOn(useTranslationStore.getState(), "showMark").mockReturnValue(null);
    await openCard();
    expect(screen.getByTestId("note-card-translation")).toHaveTextContent(
      "Marked in 繁中; the paper is showing the original",
    );
    act(() => {
      useTranslationStore.setState({ enabledByPaper: { 3: true } });
    });
    expect(screen.getByTestId("note-card-translation")).toHaveTextContent(
      "Marked in 繁中; the paper is showing 日本語",
    );
    fireEvent.click(screen.getByRole("button", { name: "Switch to 繁中" }));
    expect(showMark).toHaveBeenCalledWith("mk_1");
    showMark.mockRestore();
  });
});

describe("the notes pane beside the paper", () => {
  it("tints the block of the mark under the pointer and darkens its note's dividers", async () => {
    seed();
    render(<NoteEditor noteId={7} variant="pane" />);
    const q1 = await screen.findByTestId("paper-ref-q1");
    const d1 = screen.getByTestId("divider-d1").querySelector("hr")!;
    const d2 = screen.getByTestId("divider-d2").querySelector("hr")!;
    expect(q1).not.toHaveAttribute("data-target");
    act(() => useMarkNavStore.getState().setHovered("mk_1"));
    expect(q1).toHaveAttribute("data-target", "true");
    expect(screen.getByTestId("paper-ref-q3")).not.toHaveAttribute("data-target");
    // The second note is framed by d1 and d2.
    expect(d1).toHaveAttribute("data-edge", "true");
    expect(d2).toHaveAttribute("data-edge", "true");
    act(() => useMarkNavStore.getState().setHovered(null));
    expect(q1).not.toHaveAttribute("data-target");
    expect(d1).not.toHaveAttribute("data-edge");
  });
});
