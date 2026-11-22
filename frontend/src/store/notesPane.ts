/** The Notes pane of the chat's right panel: whether it is open, which tab
 * shows when it shares the panel with the reader, and its width beside it. */
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import { CANVAS_MIN_WIDTH, useCanvasStore } from "@/store/canvas";

export const NOTES_MIN_WIDTH = 320;
export const NOTES_DEFAULT_WIDTH = 420;
/** From this panel width, Reader and Notes sit side by side instead of tabs. */
export const SIDE_BY_SIDE_MIN = 760;
/** The divider between the reader and Notes side by side (px). */
export const DIVIDER_WIDTH = 6;

export type PaneTab = "reader" | "notes";

/** "Open in notes": the note to show and the block to bring into view. A
 * paper's note opens with its paper in the reader; a chat's on the chat tab. */
export interface NotesRequest {
  kind: "paper" | "session" | "idea";
  /** The target's id (a paper: its paper_content_id). */
  id: number;
  noteId: number;
  blockId: string | null;
  seq: number;
}

interface NotesPaneState {
  open: boolean;
  /** The pane on top when Reader (or Slides) and Notes share the panel as tabs. */
  activeTab: PaneTab;
  /** The Notes pane's width when side by side (px). */
  notesWidth: number;
  /** Opens on the Notes tab, or closes. */
  toggle: () => void;
  close: () => void;
  setActiveTab: (tab: PaneTab) => void;
  setNotesWidth: (width: number) => void;
  /** Set by `openAt`, cleared by the pane once it has shown the block. */
  request: NotesRequest | null;
  /** Opens the pane on the Notes tab at a note (and one of its blocks). */
  openAt: (target: Omit<NotesRequest, "seq">) => void;
  consumeRequest: (seq: number) => void;
  /** A paper the notes pane switches to without coming forward (a `[[`
   *  link to a paper: the reader opens it, the notes follow). */
  focus: { paperId: number; seq: number } | null;
  focusPaper: (paperId: number) => void;
  /** Per chat (its local id), the paper whose note was last on screen: the
   * pane reopens on it while the reader shows no paper. Kept for this page
   * load only. */
  lastPaperByChat: Record<number, number>;
  rememberPaper: (chatId: number, paperId: number) => void;
}

/** localStorage that never throws (private windows, blocked site data). */
const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      localStorage.setItem(name, value);
    } catch {
      /* ignore */
    }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name);
    } catch {
      /* ignore */
    }
  },
};

/** Request numbers only grow (a consumed request is null, never reused). */
let requestSeq = 0;

export const useNotesPaneStore = create<NotesPaneState>()(
  persist(
    (set) => ({
      open: false,
      activeTab: "reader",
      notesWidth: NOTES_DEFAULT_WIDTH,
      toggle: () =>
        set((s) => (s.open ? { open: false } : { open: true, activeTab: "notes" })),
      close: () => set({ open: false }),
      setActiveTab: (activeTab) => set({ activeTab }),
      setNotesWidth: (width) => set({ notesWidth: Math.max(NOTES_MIN_WIDTH, Math.round(width)) }),
      request: null,
      openAt: (target) => {
        // The paper tab shows the paper in the reader: open it there first.
        if (target.kind === "paper") useCanvasStore.getState().openPaper(target.id);
        set({ open: true, activeTab: "notes", request: { ...target, seq: ++requestSeq } });
      },
      consumeRequest: (seq) => set((s) => (s.request?.seq === seq ? { request: null } : {})),
      focus: null,
      focusPaper: (paperId) => set((s) => ({ focus: { paperId, seq: (s.focus?.seq ?? 0) + 1 } })),
      lastPaperByChat: {},
      rememberPaper: (chatId, paperId) =>
        set((s) =>
          s.lastPaperByChat[chatId] === paperId
            ? {}
            : { lastPaperByChat: { ...s.lastPaperByChat, [chatId]: paperId } },
        ),
    }),
    {
      name: "paperhub-notes-pane-v1",
      storage: createJSONStorage(() => safeStorage),
      // The layout is remembered: the pane reopens on reload with its tab
      // and width.
      partialize: (s) => ({ open: s.open, notesWidth: s.notesWidth, activeTab: s.activeTab }),
    },
  ),
);

export type PanelLayout = "single" | "tabs" | "side";

/** The widest Notes can be side by side in a panel this wide: the reader
 * keeps its minimum beside the divider. */
export function maxNotesWidth(panelWidth: number): number {
  return panelWidth - CANVAS_MIN_WIDTH - DIVIDER_WIDTH;
}

/** How the right panel lays out its panes. `panelWidth` is the dragged panel
 * width; side by side, Notes keeps its width and the reader takes the rest,
 * never less than its minimum. */
export function rightPanelLayout(p: {
  primaryOpen: boolean;
  notesOpen: boolean;
  panelWidth: number;
  notesWidth: number;
}): { layout: PanelLayout; notesWidth: number } {
  const both = p.primaryOpen && p.notesOpen;
  const layout: PanelLayout = !both ? "single" : p.panelWidth >= SIDE_BY_SIDE_MIN ? "side" : "tabs";
  const notesWidth = Math.max(
    NOTES_MIN_WIDTH,
    Math.min(p.notesWidth, maxNotesWidth(p.panelWidth)),
  );
  return { layout, notesWidth };
}
