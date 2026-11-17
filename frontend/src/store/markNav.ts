/** Between the marks on the paper and the notes (Plan H5b.1): the note card a
 * click on a mark opens (the notes pane hidden), the mark the pointer is over
 * (its block tinted in the visible notes pane), the "back to note" pill a
 * source chip leaves in the reader, and the ways to the reader at a mark — a
 * source chip, a Show ↗ jumper, My marks.
 *
 * The readers register where each drawn mark is on screen
 * (`registerMarkAnchors`), so the card can point at it and the pill can tell
 * when the user has scrolled away from it. */
import { create } from "zustand";

import { useCanvasStore } from "@/store/canvas";
import { useMarksStore } from "@/store/marks";
import { rightPanelLayout, useNotesPaneStore } from "@/store/notesPane";
import { useSlidesStore } from "@/store/slides";
import { useTranslationStore } from "@/store/translation";
import type { Mark } from "@/types/notes";

export interface NoteCardTarget {
  paperId: number;
  /** The mark clicked (its reference is the one tinted). */
  markId: string;
  /** The note of the section shown editable; null: the one quoting the mark. */
  partId: string | null;
  /** Tells two openings apart (the card re-points and glides). */
  n: number;
}

export interface BackToNote {
  paperId: number;
  markId: string;
  noteId: number;
  blockId: string;
  n: number;
}

/** A rectangle in window (client) coordinates. */
export interface ClientRect {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
}

/** Where a drawn mark is: its line boxes and the reader's visible area. */
export interface MarkAnchor {
  rects: ClientRect[];
  viewport: ClientRect;
}

/** A reader's view of its drawn marks. */
export interface MarkAnchorSource {
  find: (markId: string) => MarkAnchor | null;
  /** Calls back when the marks may have moved (the reader scrolled). */
  subscribe?: (onMove: () => void) => () => void;
}

interface MarkNavState {
  card: NoteCardTarget | null;
  /** Opens (or re-points) the note card at a mark. */
  openCard: (paperId: number, markId: string) => void;
  /** Shows another note of the card's section editable. */
  showPart: (partId: string) => void;
  closeCard: () => void;
  /** The open card's place on screen, so the translation card stacks under it. */
  cardBox: { top: number; bottom: number } | null;
  setCardBox: (box: { top: number; bottom: number } | null) => void;
  /** The mark under the pointer while the notes pane is visible. */
  hovered: string | null;
  setHovered: (markId: string | null) => void;
  back: BackToNote | null;
  showBack: (back: Omit<BackToNote, "n">) => void;
  clearBack: () => void;
}

let seq = 0;

export const useMarkNavStore = create<MarkNavState>()((set) => ({
  card: null,
  openCard: (paperId, markId) => set({ card: { paperId, markId, partId: null, n: ++seq } }),
  showPart: (partId) => set((s) => (s.card ? { card: { ...s.card, partId } } : {})),
  closeCard: () => set({ card: null }),
  cardBox: null,
  setCardBox: (box) =>
    set((s) =>
      s.cardBox?.top === box?.top && s.cardBox?.bottom === box?.bottom ? {} : { cardBox: box },
    ),
  hovered: null,
  setHovered: (markId) => set((s) => (s.hovered === markId ? {} : { hovered: markId })),
  back: null,
  showBack: (back) => set({ back: { ...back, n: ++seq } }),
  clearBack: () => set({ back: null }),
}));

// ---- Where marks are on screen ---------------------------------------------

const sources = new Map<number, Set<MarkAnchorSource>>();

/** A reader shows `paperId`'s marks: they can be pointed at. */
export function registerMarkAnchors(paperId: number, source: MarkAnchorSource): () => void {
  let set = sources.get(paperId);
  if (!set) {
    set = new Set();
    sources.set(paperId, set);
  }
  set.add(source);
  return () => {
    set.delete(source);
    if (!set.size) sources.delete(paperId);
  };
}

/** Where a mark is drawn now (the first reader of its paper that draws it). */
export function markAnchor(paperId: number, markId: string): MarkAnchor | null {
  for (const s of sources.get(paperId) ?? []) {
    const a = s.find(markId);
    if (a && a.rects.length) return a;
  }
  return null;
}

/** Calls back whenever a reader of `paperId` may have moved its marks. */
export function onMarksMoved(paperId: number, cb: () => void): () => void {
  const offs = [...(sources.get(paperId) ?? [])].map((s) => s.subscribe?.(cb) ?? (() => undefined));
  return () => offs.forEach((off) => off());
}

// ---- Paper ↔ note --------------------------------------------------------

/** Whether the notes pane is on screen beside the reader (side by side, or
 * its tab in front). */
export function notesPaneVisible(): boolean {
  const pane = useNotesPaneStore.getState();
  if (!pane.open) return false;
  const canvas = useCanvasStore.getState();
  const { layout } = rightPanelLayout({
    primaryOpen: canvas.open || useSlidesStore.getState().open,
    notesOpen: true,
    panelWidth: canvas.width,
    notesWidth: pane.notesWidth,
  });
  return layout !== "tabs" || pane.activeTab === "notes";
}

function findMark(markId: string): Mark | null {
  for (const list of Object.values(useMarksStore.getState().marksByPaper)) {
    const m = list.find((x) => x.id === markId);
    if (m) return m;
  }
  return null;
}

/** A click on a mark on the paper: with the notes pane visible, its block
 * there (scrolled to and flashed); otherwise the note card. */
export function activateMark(paperId: number, markId: string): void {
  const mark = findMark(markId);
  if (notesPaneVisible()) {
    if (mark?.note_id !== undefined) {
      useNotesPaneStore
        .getState()
        .openAt({ kind: "paper", id: paperId, noteId: mark.note_id, blockId: mark.block_id });
    }
    return;
  }
  useMarkNavStore.getState().openCard(paperId, markId);
}

/** A click on a 文 marker (a mark on a translation not on screen): with the
 * notes pane visible, translation turns on to show it; otherwise its note
 * card, which offers to switch. */
export function activateMarker(paperId: number, markId: string): void {
  if (notesPaneVisible()) {
    useTranslationStore.getState().showMark(markId);
    activateMark(paperId, markId);
    return;
  }
  useMarkNavStore.getState().openCard(paperId, markId);
}

/** The pointer is over a mark (null: off it): its block is tinted in the
 * notes pane, when the pane is visible. */
export function hoverMark(markId: string | null): void {
  useMarkNavStore.getState().setHovered(markId !== null && notesPaneVisible() ? markId : null);
}

export interface OpenInReaderOptions {
  /** The note block it was opened from: the reader shows "back to note". */
  back?: { noteId: number; blockId: string };
  /** The mark's paper, when its marks may not be loaded yet. */
  paperId?: number;
}

/** Opens the reader at a mark: its paper, in the view it was made in (the
 * other view is tried where its text is found when it isn't drawn there),
 * scrolled to it and flashed. A mark on a translation turns translation on
 * in its language first (`showMark`, which says so with a way back). */
export async function openMarkInReader(markId: string, opts: OpenInReaderOptions = {}): Promise<boolean> {
  let mark = findMark(markId);
  if (!mark && opts.paperId !== undefined) {
    await useMarksStore
      .getState()
      .loadPaper(opts.paperId)
      .catch(() => undefined);
    mark = findMark(markId);
  }
  if (!mark) return false;
  const paperId = mark.paper_content_id;
  const canvas = useCanvasStore.getState();
  canvas.openPaper(paperId);
  canvas.requestReaderTab(paperId, mark.view);
  // As tabs, the reader comes to the front (side by side, both show).
  const pane = useNotesPaneStore.getState();
  if (pane.activeTab !== "reader") pane.setActiveTab("reader");
  if (mark.lang) useTranslationStore.getState().showMark(markId);
  else useTranslationStore.getState().requestReveal(paperId, markId, { fallback: true });
  if (opts.back) useMarkNavStore.getState().showBack({ paperId, markId, ...opts.back });
  return true;
}

/** A Show ↗ jumper in the note card: the paper scrolls to that mark, it
 * flashes, and the card points at it. */
export function jumpToMark(paperId: number, markId: string): void {
  void openMarkInReader(markId, { paperId }).then((ok) => {
    if (ok) useMarkNavStore.getState().openCard(paperId, markId);
  });
}
