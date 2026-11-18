/** Between the marks on the paper and the notes (Plan H5b.1): the note card a
 * click on a mark opens (the notes pane hidden), the mark the pointer is over
 * (its block tinted in the visible notes pane), and the ways to the reader
 * at a mark — a source chip, a Show ↗ jumper, My marks.
 *
 * The readers register where each drawn mark is on screen
 * (`registerMarkAnchors`), so the card can point at it. */
import type { TFunction } from "i18next";
import { toast } from "sonner";
import { create } from "zustand";

import i18n from "@/lib/i18n";

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
  /** Which view of the paper it is (an HTML paper has both, one on screen). */
  kind: "html" | "pdf";
  find: (markId: string) => MarkAnchor | null;
  /** The reader's visible area (where an unplaced card docks). */
  viewport: () => ClientRect | null;
  /** Calls back when the marks may have moved: the reader scrolled, its box
   * moved or resized, a page drew its marks again. */
  subscribe?: (onMove: () => void) => () => void;
}

/** The open card's place: where it is and which side of the paragraph. */
export interface CardBox {
  top: number;
  bottom: number;
  side: "left" | "below" | "above";
}

interface MarkNavState {
  card: NoteCardTarget | null;
  /** Opens (or re-points) the note card at a mark. */
  openCard: (paperId: number, markId: string) => void;
  /** Shows another note of the card's section editable. */
  showPart: (partId: string) => void;
  closeCard: () => void;
  /** The open card's place on screen, so the translation card stacks under it. */
  cardBox: CardBox | null;
  setCardBox: (box: CardBox | null) => void;
  /** The mark under the pointer while the notes pane is visible. */
  hovered: string | null;
  setHovered: (markId: string | null) => void;
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
      s.cardBox?.top === box?.top && s.cardBox?.bottom === box?.bottom && s.cardBox?.side === box?.side
        ? {}
        : { cardBox: box },
    ),
  hovered: null,
  setHovered: (markId) => set((s) => (s.hovered === markId ? {} : { hovered: markId })),
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

/** The readers of `paperId` on screen: the view the reader shows (an HTML
 * paper keeps its other view mounted off screen). With no view published
 * (a reader outside the chat), every reader of the paper. */
function onScreen(paperId: number): MarkAnchorSource[] {
  const view = useCanvasStore.getState().readerView;
  const all = [...(sources.get(paperId) ?? [])];
  if (!view) return all;
  return view.paperId === paperId ? all.filter((s) => s.kind === view.kind) : [];
}

/** Where a mark is drawn now, in the view on screen (null: not drawn there). */
export function markAnchor(paperId: number, markId: string): MarkAnchor | null {
  for (const s of onScreen(paperId)) {
    const a = s.find(markId);
    if (a && a.rects.length) return a;
  }
  return null;
}

/** The visible area of the reader on screen showing `paperId`. */
export function readerViewport(paperId: number): ClientRect | null {
  for (const s of onScreen(paperId)) {
    const v = s.viewport();
    if (v) return v;
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
 * there (scrolled to and flashed; the paper's marks are loaded first when
 * they aren't yet); otherwise the note card. */
export function activateMark(paperId: number, markId: string): void {
  if (!notesPaneVisible()) {
    useMarkNavStore.getState().openCard(paperId, markId);
    return;
  }
  const open = (mark: Mark | null) => {
    if (mark?.note_id === undefined) return false;
    useNotesPaneStore
      .getState()
      .openAt({ kind: "paper", id: paperId, noteId: mark.note_id, blockId: mark.block_id });
    return true;
  };
  if (open(findMark(markId))) return;
  void useMarksStore
    .getState()
    .loadPaper(paperId)
    .then(() => {
      if (!open(findMark(markId))) toast.message(t()("notes:marks.noteMissing", "This mark's note isn't available."));
    })
    .catch(() => toast.error(t()("notes:marks.noteMissing", "This mark's note isn't available.")));
}

/** A click on a 文 marker (a mark on a translation not on screen):
 * translation turns on to show it. With the notes pane visible its block
 * opens there too; otherwise its note card opens once the reader has shown
 * the mark (or has said why it can't: the card then opens unplaced). */
export function activateMarker(paperId: number, markId: string): void {
  const tr = useTranslationStore;
  tr.getState().showMark(markId);
  if (notesPaneVisible()) {
    activateMark(paperId, markId);
    return;
  }
  openCardAfterReveal(paperId, markId);
}

/** Opens the mark's card once the reader has shown the mark (or said why it
 * can't): opened earlier, while the reader still slides to the other view,
 * the card finds no anchor and falls back to a place over the chat. */
function openCardAfterReveal(paperId: number, markId: string): void {
  const tr = useTranslationStore;
  const reveal = tr.getState().reveal;
  if (!reveal || reveal.markId !== markId) {
    useMarkNavStore.getState().openCard(paperId, markId);
    return;
  }
  const off = tr.subscribe((s) => {
    if (s.reveal?.n === reveal.n) return;
    off();
    useMarkNavStore.getState().openCard(paperId, markId);
  });
}

const t = () => i18n.t.bind(i18n) as TFunction;

/** Jumping to a mark whose colour is hidden (or with "Show my marks" off):
 * it shows for now, with a toast to hide it again. Nothing is saved. */
function showHiddenFor(mark: Mark): void {
  const marks = useMarksStore.getState();
  const color = mark.color;
  const visible = color !== null && marks.colors.some((c) => c.color === color && c.visible);
  // Hidden now, or shown only for the moment by an earlier jump.
  const hiddenColor =
    color !== null && marks.colors.some((c) => c.color === color) && (!visible || marks.shownForNow.colors.includes(color))
      ? color
      : null;
  const marksOff = !marks.showMarks || marks.shownForNow.marks;
  if (!hiddenColor && !marksOff) return;
  const setVisible = (on: boolean) =>
    useMarksStore.setState((s) => ({
      colors: hiddenColor ? s.colors.map((c) => (c.color === hiddenColor ? { ...c, visible: on } : c)) : s.colors,
      ...(marksOff ? { showMarks: on } : {}),
      shownForNow: {
        colors: hiddenColor
          ? [...s.shownForNow.colors.filter((c) => c !== hiddenColor), ...(on ? [hiddenColor] : [])]
          : s.shownForNow.colors,
        marks: marksOff ? on : s.shownForNow.marks,
      },
    }));
  setVisible(true);
  toast(t()("notes:marks.showingHidden", "Showing a hidden colour"), {
    id: "marks-showing-hidden",
    action: { label: t()("notes:marks.hideAgain", "Hide again"), onClick: () => setVisible(false) },
  });
}

/** The pointer is over a mark (null: off it): its block is tinted in the
 * notes pane, when the pane is visible. */
export function hoverMark(markId: string | null): void {
  useMarkNavStore.getState().setHovered(markId !== null && notesPaneVisible() ? markId : null);
}

export interface OpenInReaderOptions {
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
  showHiddenFor(mark);
  if (mark.lang) useTranslationStore.getState().showMark(markId);
  else useTranslationStore.getState().requestReveal(paperId, markId, { fallback: true });
  return true;
}

// The card belongs to the paper in the reader: closing the reader, or
// showing another paper there, closes it (with its exit).
useCanvasStore.subscribe((s) => {
  const card = useMarkNavStore.getState().card;
  if (!card) return;
  if (!s.open || (s.activePaperId !== null && s.activePaperId !== card.paperId)) {
    useMarkNavStore.getState().closeCard();
  }
});

/** A Show ↗ jumper in the note card: the paper scrolls to that mark, it
 * flashes, and the card points at it. */
export function jumpToMark(paperId: number, markId: string): void {
  void openMarkInReader(markId, { paperId }).then((ok) => {
    if (ok) openCardAfterReveal(paperId, markId);
  });
}
