/** The Notes pane of the chat's right panel: whether it is open, which tab
 * shows when it shares the panel with the reader, and its width beside it. */
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import { CANVAS_MIN_WIDTH } from "@/store/canvas";

export const NOTES_MIN_WIDTH = 320;
export const NOTES_DEFAULT_WIDTH = 420;
/** From this panel width, Reader and Notes sit side by side instead of tabs. */
export const SIDE_BY_SIDE_MIN = 760;

export type PaneTab = "reader" | "notes";

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
    }),
    {
      name: "paperhub-notes-pane-v1",
      storage: createJSONStorage(() => safeStorage),
      // Like the reader, the pane does not reopen on reload; its width and
      // last tab are remembered.
      partialize: (s) => ({ notesWidth: s.notesWidth, activeTab: s.activeTab }),
    },
  ),
);

export type PanelLayout = "single" | "tabs" | "side";

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
    Math.min(p.notesWidth, p.panelWidth - CANVAS_MIN_WIDTH),
  );
  return { layout, notesWidth };
}
