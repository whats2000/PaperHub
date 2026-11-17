import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackToNotePill } from "@/components/canvas/BackToNotePill";
import { NoteEditor } from "@/components/notes/NoteEditor";
import { useCanvasStore } from "@/store/canvas";
import { registerMarkAnchors, useMarkNavStore, type ClientRect } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import { useTranslationStore } from "@/store/translation";
import type { Mark, NoteBlock } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listMarkColors: vi.fn(() => Promise.resolve([])),
}));
const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: vi.fn(), message: vi.fn(), success: vi.fn(), dismiss: vi.fn() }),
}));

const QUOTE: NoteBlock = {
  id: "q1",
  note_id: 7,
  parent_id: null,
  type: "paperQuote",
  props: {
    markId: "mk_1",
    color: "green",
    kind: "highlight",
    quote: "attention is all you need",
    pieces: [{ quote: "attention is all you need", page: 2 }],
    source: { page: 2, section: "2 Method" },
  },
  content: null,
  text: "attention is all you need",
  author: "user",
  section_key: "2 Method",
  changed_rev: 1,
};

const MARK: Mark = {
  id: "mk_1",
  paper_content_id: 3,
  block_id: "q1",
  kind: "highlight",
  color: "green",
  view: "pdf",
  pieces: [{ quote: "attention is all you need", page: 2 }],
  lang: null,
  source_ref: null,
  section_key: "2 Method",
  status: "ok",
  created_at: "2026-11-16T00:00:00Z",
  deleted_at: null,
  note_id: 7,
};

const rect = (top: number, height: number): ClientRect => ({
  top,
  height,
  bottom: top + height,
  left: 500,
  right: 900,
  width: 400,
});

let markTop = 300;
let unregister: (() => void) | null = null;

beforeEach(() => {
  markTop = 300;
  useMarkNavStore.setState({ card: null, back: null, hovered: null });
  useMarksStore.setState({ marksByPaper: { 3: [MARK] } });
  useNotesStore.setState({
    notes: {
      7: {
        note: {
          id: 7,
          target_kind: "paper",
          target_id: "3",
          title: "Attention",
          revision: 4,
          updated_at: "2026-11-16T00:00:00Z",
        },
        blocks: [QUOTE],
        suggestions: [],
        comments: [],
        revision: 4,
      },
    },
    byTarget: { "paper:3": 7 },
    reloads: {},
    colorNames: {},
  });
  // The reader shows its view 100–700; the mark starts in it.
  unregister = registerMarkAnchors(3, {
    find: () => ({ rects: [rect(markTop, 18)], viewport: { ...rect(100, 600), left: 500 } }),
  });
});
afterEach(() => {
  unregister?.();
  clearPendingNoteOps();
});

describe("a note's source chip", () => {
  it("opens the reader at the mark, in the view it was made in, and leaves a way back to the note", async () => {
    const openPaper = vi.spyOn(useCanvasStore.getState(), "openPaper");
    const tab = vi.spyOn(useCanvasStore.getState(), "requestReaderTab");
    const reveal = vi.spyOn(useTranslationStore.getState(), "requestReveal");
    render(<NoteEditor noteId={7} variant="pane" />);
    const chip = await screen.findByRole("button", { name: /^Open in the paper: Attention/ });
    fireEvent.click(chip);
    await waitFor(() => expect(reveal).toHaveBeenCalledWith(3, "mk_1", { fallback: true }));
    expect(openPaper).toHaveBeenCalledWith(3);
    expect(tab).toHaveBeenCalledWith(3, "pdf");
    expect(useMarkNavStore.getState().back).toMatchObject({ paperId: 3, markId: "mk_1", noteId: 7, blockId: "q1" });
    openPaper.mockRestore();
    tab.mockRestore();
    reveal.mockRestore();
  });

  it("asks the translation to show a mark made on a translation", async () => {
    useMarksStore.setState({ marksByPaper: { 3: [{ ...MARK, lang: "繁體中文", view: "html" }] } });
    const showMark = vi.spyOn(useTranslationStore.getState(), "showMark").mockReturnValue(null);
    render(<NoteEditor noteId={7} variant="pane" />);
    fireEvent.click(await screen.findByRole("button", { name: /^Open in the paper: Attention/ }));
    await waitFor(() => expect(showMark).toHaveBeenCalledWith("mk_1"));
    showMark.mockRestore();
  });
});

describe("back to note", () => {
  function showBack() {
    act(() => useMarkNavStore.getState().showBack({ paperId: 3, markId: "mk_1", noteId: 7, blockId: "q1" }));
  }

  it("returns to the note block in one click", () => {
    const openAt = vi.fn();
    useNotesPaneStore.setState({ openAt });
    render(<BackToNotePill paperId={3} />);
    expect(screen.queryByTestId("back-to-note")).toBeNull();
    showBack();
    const pill = screen.getByRole("button", { name: "Back to note" });
    expect(pill).toHaveAttribute("data-state", "open");
    fireEvent.click(pill);
    expect(openAt).toHaveBeenCalledWith({ kind: "paper", id: 3, noteId: 7, blockId: "q1" });
    expect(useMarkNavStore.getState().back).toBeNull();
  });

  it("fades out once the user scrolls about a screen away from the mark", async () => {
    render(<BackToNotePill paperId={3} />);
    showBack();
    const pill = screen.getByRole("button", { name: "Back to note" });
    // Near the mark: it stays.
    markTop = 400;
    act(() => {
      window.dispatchEvent(new Event("scroll"));
    });
    await new Promise((r) => setTimeout(r, 300));
    expect(useMarkNavStore.getState().back).not.toBeNull();
    // More than a screen away: it fades.
    markTop = -1200;
    act(() => {
      window.dispatchEvent(new Event("scroll"));
    });
    await waitFor(() => expect(useMarkNavStore.getState().back).toBeNull());
    expect(pill).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(screen.queryByTestId("back-to-note")).toBeNull());
  });

  it("is not shown over another paper", () => {
    render(<BackToNotePill paperId={4} />);
    showBack();
    expect(screen.queryByTestId("back-to-note")).toBeNull();
  });
});
