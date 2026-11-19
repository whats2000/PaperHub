import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NoteCard } from "@/components/canvas/NoteCard";
import { NoteEditor } from "@/components/notes/NoteEditor";
import { deleteMark, restoreMark } from "@/lib/api";
import { CANVAS_DEFAULT_WIDTH, useCanvasStore } from "@/store/canvas";
import {
  activateMark,
  activateMarker,
  jumpToMark,
  registerMarkAnchors,
  useMarkNavStore,
  type ClientRect,
} from "@/store/markNav";
import { resetMarkOps, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { clearPendingNoteOps, pendingNoteOps, useNotesStore } from "@/store/notes";
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
      kind: "text", style: "highlight",
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
    kind: "text", style: "highlight",
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
/** Where each mark is drawn (its top; null: not drawn) in the HTML view. */
let anchorTop: Record<string, number | null> = {};
/** The reader's visible area: the gutter left of it holds the card. */
const VIEWPORT = rect(40, 800, 600, 700);
/** The reader's "marks moved" callback, as the card subscribed to it. */
let moved: (() => void) | null = null;

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
    kind: "html",
    viewport: () => VIEWPORT,
    find: (id) => {
      const top = anchorTop[id];
      return top == null ? null : { rects: [rect(top, 820, 300, 18)], viewport: VIEWPORT };
    },
    subscribe: (cb) => {
      moved = cb;
      return () => {
        moved = null;
      };
    },
  });
}

beforeEach(() => {
  toastFn.mockReset();
  resetMarkOps();
  anchorTop = { mk_1: 200, mk_2: 120, mk_3: 230 };
  moved = null;
  // First: a reveal left waiting by the last test settles before the reset.
  useTranslationStore.setState({ reveal: null });
  useMarkNavStore.setState({ card: null, hovered: null, cardBox: null });
  useNotesPaneStore.setState({ open: false, activeTab: "reader" });
  useCanvasStore.setState({ open: true, width: CANVAS_DEFAULT_WIDTH, readerView: null, activePaperId: 3 });
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
  it("in a note without section headings, shows the blocks of the mark's section", async () => {
    // [user, 2026-11-19] Marking adds no headings.
    seed();
    const intro = blk("qi", {
      type: "paperQuote", section_key: null,
      props: { markId: "mk_i", color: "green", kind: "text", style: "highlight", quote: "intro words",
        pieces: [{ quote: "intro words", page: 1 }], source: { page: 1, section: "1 Introduction" } },
      text: "intro words",
    });
    const bare = [intro, para("pi", "Intro thoughts"), ref("q1", "mk_1", "attention is all you need"), para("p2", "My thoughts")];
    useNotesStore.setState((s) => ({ notes: { 7: { ...s.notes[7]!, blocks: bare } } }));
    await openCard();
    expect(screen.getByTestId("note-card-title")).toHaveTextContent("Attention Is All You Need› §2");
    await waitFor(() => expect(outer("p2")).not.toBeNull());
    expect(outer("q1")?.getAttribute("data-card-hidden")).not.toBe("true");
    expect(outer("p2")?.getAttribute("data-card-hidden")).not.toBe("true");
    expect(outer("qi")?.getAttribute("data-card-hidden")).toBe("true");
    expect(outer("pi")?.getAttribute("data-card-hidden")).toBe("true");
  });

  it("names the paper, the section and which note of it, in the gutter left of the reader", async () => {
    seed();
    const card = await openCard();
    expect(screen.getByTestId("note-card-title")).toHaveTextContent("Attention Is All You Need› §2 · note 2 of 3");
    // Beside the reader, never over it.
    expect(parseFloat(card.style.left) + parseFloat(card.style.width)).toBeLessThanOrEqual(800);
    expect(useMarkNavStore.getState().cardBox).not.toBeNull();
  });

  it("a long section name truncates with the title, never under the buttons", async () => {
    seed();
    await openCard();
    const bar = screen.getByTestId("note-card-title");
    const [title, where] = Array.from(bar.children) as HTMLElement[];
    // Both parts shrink; the title keeps a share (it is never pushed to zero).
    expect(title!.className).toMatch(/truncate/);
    expect(where!.className).toMatch(/truncate/);
    expect(where!.className).not.toMatch(/shrink-0/);
    expect(title!.className).toMatch(/min-w-\[/);
    expect(bar.className).toMatch(/overflow-hidden/);
    expect(bar.getAttribute("title") ?? bar.getAttribute("aria-label") ?? "").toBeDefined();
  });

  it("a section named like the paper shows the name once", async () => {
    seed();
    useNotesStore.setState((s) => ({
      notes: { 7: { ...s.notes[7]!, note: { ...s.notes[7]!.note, title: " 2  method" } } },
    }));
    await openCard();
    const text = screen.getByTestId("note-card-title").textContent ?? "";
    expect(text).toBe(" 2  method· note 2 of 3"); // the parts sit a gap apart
    expect(text).not.toContain("§");
  });

  it("is a sticky note tinted with the mark's colour; a plain quote gets warm paper", async () => {
    seed([mark("mk_1", "q1"), mark("mk_2", "q2", { color: null, kind: "text", style: "underline" }), mark("mk_3", "q3")]);
    const card = await openCard();
    expect(card.className).toMatch(/note-card-sticky/);
    expect(card.style.getPropertyValue("--sticky-mc")).toBe("var(--hl-green)");
    act(() => useMarkNavStore.getState().openCard(3, "mk_2"));
    await waitFor(() => expect(card.style.getPropertyValue("--sticky-mc")).toBe("var(--sticky-base)"));
  });

  it("shows what the mark's colour means as a chip; a custom colour tints it too", async () => {
    seed([mark("mk_1", "q1", { color: "c_teal" }), mark("mk_2", "q2", { color: null, kind: "text", style: "wavy" })]);
    useMarksStore.setState({
      colors: [
        { color: "green", name: "Key result", visible: true },
        { color: "c_teal", name: "To verify", visible: true, hex: "#1f8a8a", dark: "#2a9a9a" },
      ],
    });
    const card = await openCard();
    expect(card.style.getPropertyValue("--sticky-mc")).toBe("var(--hl-c_teal)");
    expect(within(card).getByTestId("note-card-color")).toHaveAccessibleName("Colour: To verify");
    // No colour: neutral paper and no chip.
    act(() => useMarkNavStore.getState().openCard(3, "mk_2"));
    await waitFor(() => expect(within(card).queryByTestId("note-card-color")).toBeNull());
  });

  it("opened after a highlight, the caret is on a new empty line under the quote; left empty, nothing is saved", async () => {
    seed();
    useNotesStore.getState().setCaretAfter({ noteId: 7, blockId: "q3", in: "card" });
    await openCard();
    await waitFor(() => {
      const sel = window.getSelection();
      const line = sel?.anchorNode?.parentElement?.closest(".bn-block-outer");
      expect(line?.previousElementSibling?.getAttribute("data-id")).toBe("q3");
    });
    expect(useNotesStore.getState().caretAfter).toBeNull();
    act(() => useMarkNavStore.getState().closeCard());
    await new Promise((r) => setTimeout(r, 50));
    expect(pendingNoteOps(7)).toEqual([]);
  });

  it("has one scroll area, its body: a long paragraph grows the card, never an inner scrollbar", async () => {
    const long = "word ".repeat(600);
    seed();
    useNotesStore.setState((s) => ({
      notes: { 7: { ...s.notes[7]!, blocks: s.notes[7]!.blocks.map((b) => (b.id === "p2" ? para("p2", long) : b)) } },
    }));
    const card = await openCard();
    await waitFor(() => expect(outer("p2")).not.toBeNull());
    const scrollers = Array.from(card.querySelectorAll<HTMLElement>("*")).filter(
      (el) =>
        /(^|\s)overflow(-y)?-(auto|scroll)(\s|$)/.test(el.getAttribute("class") ?? "") ||
        /overflow(-y)?:\s*(auto|scroll)/.test(el.getAttribute("style") ?? ""),
    );
    expect(scrollers).toHaveLength(1);
    expect(scrollers[0]!.contains(outer("p2"))).toBe(true);
  });

  it("sizes to the gutter: 30% of the window (260–400px), as tall as the reader less 20px each side", async () => {
    seed();
    const card = await openCard();
    const want = Math.min(400, Math.max(260, window.innerWidth * 0.3));
    expect(parseFloat(card.style.width)).toBeCloseTo(want, 0);
    expect(parseFloat(card.style.maxHeight)).toBe(VIEWPORT.height - 40);
    expect(parseFloat(card.style.left) + parseFloat(card.style.width)).toBeLessThanOrEqual(VIEWPORT.left);
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
    // The reader shows the passage, then the card re-points.
    await waitFor(() => expect(useTranslationStore.getState().reveal?.markId).toBe("mk_3"));
    act(() => useTranslationStore.getState().revealed(useTranslationStore.getState().reveal!.n));
    await waitFor(() => expect(useMarkNavStore.getState().card?.markId).toBe("mk_3"));
    expect(reveal).toHaveBeenCalledWith(3, "mk_3", { fallback: true });
    await waitFor(() => expect(screen.getByTestId("paper-ref-q3")).toHaveAttribute("data-target", "true"));
    expect(screen.getByTestId("paper-ref-q1")).not.toHaveAttribute("data-target");
    reveal.mockRestore();
  });

  it("deletes the mark from its trash, with Undo (no confirmation after)", async () => {
    seed();
    await openCard();
    fireEvent.click(screen.getByRole("button", { name: "Delete this mark" }));
    expect(useMarkNavStore.getState().card).toBeNull();
    await waitFor(() => expect(deleteMark).toHaveBeenCalledWith("mk_1", expect.anything()));
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith("Removed from the paper and the note", expect.anything()));
    expect(useMarksStore.getState().marksByPaper[3]!.map((m) => m.id)).not.toContain("mk_1");
    const undo = (toastFn.mock.calls.at(-1)![1] as { action: { label: string; onClick: () => void } }).action;
    expect(undo.label).toBe("Undo");
    act(() => undo.onClick());
    await waitFor(() => expect(restoreMark).toHaveBeenCalledWith("mk_1", expect.anything()));
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]!.map((m) => m.id)).toContain("mk_1"));
    expect(toastFn).not.toHaveBeenCalledWith("Mark restored", expect.anything());
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
    anchorTop.mk_1 = -900;
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

describe("the note card, fix round 1", () => {
  it("offers to turn translation on when it is off, and to switch when another language shows", async () => {
    seed([mark("mk_1", "q1", { lang: "繁體中文" }), mark("mk_3", "q3")]);
    useTranslationStore.setState({ enabledByPaper: { 3: false }, language: "日本語" });
    await openCard();
    expect(screen.getByRole("button", { name: "Turn on translation" })).toBeInTheDocument();
    act(() => {
      useTranslationStore.setState({ enabledByPaper: { 3: true } });
    });
    expect(screen.getByRole("button", { name: "Switch to 繁中" })).toBeInTheDocument();
  });

  it("points at the view on screen, never at the paper's other view kept off screen", async () => {
    seed();
    // The PDF view, off to the right of the reader, also draws mk_1.
    const pdfView = rect(40, 1600, 600, 700);
    const offPdf = registerMarkAnchors(3, {
      kind: "pdf",
      viewport: () => pdfView,
      find: () => ({ rects: [rect(500, 1620, 300, 18)], viewport: pdfView }),
    });
    useCanvasStore.setState({ readerView: { paperId: 3, kind: "html" } });
    const card = await openCard();
    expect(parseFloat(card.style.top)).toBe(200);
    expect(parseFloat(card.style.left) + parseFloat(card.style.width)).toBeLessThanOrEqual(800);
    // The PDF tab in front: the card follows that view.
    act(() => {
      useCanvasStore.setState({ readerView: { paperId: 3, kind: "pdf" } });
    });
    await waitFor(() => expect(parseFloat(card.style.top)).toBe(500));
    offPdf();
  });

  it("re-points to a mark not on screen: docked at the top of the gutter, no arrow, and it says so", async () => {
    seed();
    const card = await openCard();
    expect(screen.getByTestId("note-card-arrow")).not.toHaveAttribute("data-unplaced");
    anchorTop.mk_3 = null;
    act(() => useMarkNavStore.getState().openCard(3, "mk_3"));
    await waitFor(() => expect(screen.getByTestId("note-card-arrow")).toHaveAttribute("data-unplaced", "true"));
    expect(parseFloat(card.style.top)).toBe(40 + 8);
    expect(screen.getByTestId("note-card-offscreen").parentElement!.parentElement).toHaveAttribute("data-state", "open");
    expect(screen.getByTestId("note-card-offscreen")).toHaveTextContent("The mark is off screen");
    // It stays open (no silent close).
    await new Promise((r) => setTimeout(r, 1400));
    expect(useMarkNavStore.getState().card?.markId).toBe("mk_3");
    const reveal = vi.spyOn(useTranslationStore.getState(), "requestReveal");
    fireEvent.click(within(screen.getByTestId("note-card-offscreen")).getByRole("button", { name: /Show/ }));
    await waitFor(() => expect(reveal).toHaveBeenCalledWith(3, "mk_3", { fallback: true }));
    // Drawn now: it points at it.
    anchorTop.mk_3 = 230;
    act(() => moved?.());
    await waitFor(() => expect(screen.getByTestId("note-card-arrow")).not.toHaveAttribute("data-unplaced"));
    expect(parseFloat(card.style.top)).toBe(230);
    reveal.mockRestore();
  });

  it("re-places when the reader says its marks moved (a resize, a page drawn again)", async () => {
    seed();
    const card = await openCard();
    expect(parseFloat(card.style.top)).toBe(200);
    anchorTop.mk_1 = 340;
    act(() => moved?.());
    await waitFor(() => expect(parseFloat(card.style.top)).toBe(340));
  });

  it("keeps its place while the mark is briefly gone (a page re-rendering)", async () => {
    seed();
    const card = await openCard();
    anchorTop.mk_1 = null;
    act(() => moved?.());
    await new Promise((r) => setTimeout(r, 50));
    expect(parseFloat(card.style.top)).toBe(200);
    expect(screen.getByTestId("note-card-arrow")).not.toHaveAttribute("data-unplaced");
  });

  it("closes when the reader closes or shows another paper", async () => {
    seed();
    await openCard();
    act(() => {
      useCanvasStore.setState({ activePaperId: 4 });
    });
    expect(useMarkNavStore.getState().card).toBeNull();
    act(() => {
      useCanvasStore.setState({ activePaperId: 3 });
      useMarkNavStore.getState().openCard(3, "mk_1");
    });
    act(() => {
      useCanvasStore.setState({ open: false });
    });
    expect(useMarkNavStore.getState().card).toBeNull();
  });

  it("does not reload the note on close: the card's edits already show in the pane, nothing lost", async () => {
    seed([mark("mk_1", "q1"), mark("mk_3", "q3")]);
    const loadNote = useNotesStore.getState().loadNote;
    render(<NoteEditor noteId={7} variant="pane" />);
    await screen.findByTestId("paper-ref-q1");
    await openCard();
    const card = screen.getByTestId("note-card");
    type Pm = HTMLElement & {
      editor: {
        commands: { insertContentAt: (pos: number, text: string) => boolean };
        state: { doc: { descendants: (f: (n: { textContent: string; isText: boolean }, pos: number) => void) => void } };
      };
    };
    const endOf = (pm: Pm, text: string) => {
      let at = -1;
      pm.editor.state.doc.descendants((n, pos) => {
        if (n.isText && n.textContent === text) at = pos + text.length;
      });
      return at;
    };
    await waitFor(() => expect(within(card).getByText("My thoughts")).toBeInTheDocument());
    const cardPm = card.querySelector(".ProseMirror") as Pm;
    act(() => {
      cardPm.editor.commands.insertContentAt(endOf(cardPm, "My thoughts"), "!");
    });
    // The pane shows it at once, from the shared store.
    const pane = screen.getAllByTestId("note-editor").find((e) => !card.contains(e))!;
    await waitFor(() => expect(within(pane).getByText("My thoughts!")).toBeInTheDocument());
    expect(useNotesStore.getState().notes[7]!.blocks.find((b) => b.id === "p2")!.text).toBe("My thoughts!");
    // Then typed in the pane: built on the card's text, not an older copy.
    const panePm = pane.querySelector(".ProseMirror") as Pm;
    act(() => {
      panePm.editor.commands.insertContentAt(endOf(panePm, "My thoughts!"), "?");
    });
    expect(useNotesStore.getState().notes[7]!.blocks.find((b) => b.id === "p2")!.text).toBe("My thoughts!?");
    await waitFor(() => expect(within(card).getByText("My thoughts!?")).toBeInTheDocument());
    fireEvent.click(within(card).getByRole("button", { name: "Close" }));
    await new Promise((r) => setTimeout(r, 200));
    expect(loadNote).not.toHaveBeenCalled();
  });
});

describe("a click on the paper", () => {
  it("with the pane visible, waits for the paper's marks to load, then opens the block", async () => {
    seed([]);
    const openAt = vi.fn();
    useNotesPaneStore.setState({ open: true, openAt });
    useCanvasStore.setState({ width: 900 });
    const loadPaper = vi.fn(() => {
      useMarksStore.setState({ marksByPaper: { 3: [mark("mk_1", "q1")] } });
      return Promise.resolve();
    });
    useMarksStore.setState({ marksByPaper: {}, loadPaper });
    activateMark(3, "mk_1");
    await waitFor(() => expect(openAt).toHaveBeenCalledWith({ kind: "paper", id: 3, noteId: 7, blockId: "q1" }));
    expect(loadPaper).toHaveBeenCalledWith(3);
  });

  it("on a 文 marker with the pane hidden, shows the mark in the translation first, then opens its card", () => {
    seed([mark("mk_1", "q1", { lang: "繁體中文" })]);
    useTranslationStore.setState({ enabledByPaper: { 3: false }, language: "日本語", reveal: null });
    activateMarker(3, "mk_1");
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(true);
    const reveal = useTranslationStore.getState().reveal!;
    expect(reveal.markId).toBe("mk_1");
    expect(useMarkNavStore.getState().card).toBeNull();
    // The reader has shown it (or said why it can't): the card opens.
    act(() => useTranslationStore.getState().revealed(reveal.n));
    expect(useMarkNavStore.getState().card).toMatchObject({ paperId: 3, markId: "mk_1" });
  });
});

describe("Show ↗ (jumpToMark)", () => {
  it("opens the card only after the reader has revealed the mark", async () => {
    seed([mark("mk_1", "q1", { view: "pdf" })]);
    useTranslationStore.setState({ reveal: null });
    jumpToMark(3, "mk_1");
    await waitFor(() => expect(useTranslationStore.getState().reveal?.markId).toBe("mk_1"));
    // Still sliding to the PDF and scrolling: no card yet (it would fall
    // back to a place over the chat).
    expect(useMarkNavStore.getState().card).toBeNull();
    const n = useTranslationStore.getState().reveal!.n;
    act(() => useTranslationStore.getState().revealed(n));
    expect(useMarkNavStore.getState().card).toMatchObject({ paperId: 3, markId: "mk_1" });
  });
});

describe("two editors on one note, fix round 2", () => {
  type Pm = HTMLElement & {
    editor: {
      commands: { insertContentAt: (pos: number, text: string) => boolean };
      state: { doc: { descendants: (f: (n: { textContent: string; isText: boolean }, pos: number) => void) => void } };
    };
  };
  const endOf = (pm: Pm, text: string) => {
    let at = -1;
    pm.editor.state.doc.descendants((n, pos) => {
      if (n.isText && n.textContent === text) at = pos + text.length;
    });
    return at;
  };

  it("keeps a pane edit and a card edit to other blocks, each queued once", async () => {
    seed([mark("mk_1", "q1"), mark("mk_3", "q3")]);
    render(<NoteEditor noteId={7} variant="pane" />);
    await screen.findByTestId("paper-ref-q1");
    await openCard();
    const card = screen.getByTestId("note-card");
    const pane = screen.getAllByTestId("note-editor").find((e) => !card.contains(e))!;
    await waitFor(() => expect(within(card).getByText("My thoughts")).toBeInTheDocument());
    const panePm = pane.querySelector(".ProseMirror") as Pm;
    const cardPm = card.querySelector(".ProseMirror") as Pm;
    // Typed in the pane (not sent yet), then in the card on another block.
    act(() => {
      panePm.editor.commands.insertContentAt(endOf(panePm, "First note words"), " A");
    });
    act(() => {
      cardPm.editor.commands.insertContentAt(endOf(cardPm, "My thoughts"), " B");
    });
    const blocks = useNotesStore.getState().notes[7]!.blocks;
    expect(blocks.find((b) => b.id === "p1")!.text).toBe("First note words A");
    expect(blocks.find((b) => b.id === "p2")!.text).toBe("My thoughts B");
    await waitFor(() => expect(within(pane).getByText("My thoughts B")).toBeInTheDocument());
    expect(within(pane).getByText("First note words A")).toBeInTheDocument();
    // One update per block waits to be sent: nothing sent twice.
    const ops = pendingNoteOps(7);
    expect(ops.filter((o) => o.block_id === "p1")).toHaveLength(1);
    expect(ops.filter((o) => o.block_id === "p2")).toHaveLength(1);
    expect(ops).toHaveLength(2);
  });

  it("replaces the whole document when another editor's block can't be placed in this one", async () => {
    seed([mark("mk_1", "q1"), mark("mk_3", "q3")]);
    render(<NoteEditor noteId={7} variant="pane" />);
    await screen.findByTestId("paper-ref-q1");
    // Another editor nested a new line under "My thoughts": an only child,
    // with no sibling here to put it beside.
    const child: NoteBlock = {
      ...BLOCKS.find((b) => b.id === "p2")!,
      id: "n1",
      parent_id: "p2",
      content: [{ type: "text", text: "A nested line", styles: {} }],
      text: "A nested line",
    };
    act(() => {
      useNotesStore.setState((s) => ({
        notes: { ...s.notes, 7: { ...s.notes[7]!, blocks: [...s.notes[7]!.blocks, child] } },
        localEdits: { 7: { seq: 99, origin: "another-editor", blockIds: ["n1"] } },
      }));
    });
    await waitFor(() => expect(screen.getByText("A nested line")).toBeInTheDocument());
    // Everything else is still there, and the change is not sent back.
    expect(screen.getByText("My thoughts")).toBeInTheDocument();
    expect(screen.getByTestId("paper-ref-q1")).toBeInTheDocument();
    expect(pendingNoteOps(7)).toHaveLength(0);
  });
});
