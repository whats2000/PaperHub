import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { DeckEventData } from "@/types/domain";

/** Filmstrip rail width bounds (px). */
export const FILMSTRIP_MIN_WIDTH = 56;
export const FILMSTRIP_MAX_WIDTH = 280;
export const FILMSTRIP_DEFAULT_WIDTH = 80;

/** Speaker note pane height bounds (px). */
export const NOTE_MIN_HEIGHT = 80;
export const NOTE_DEFAULT_HEIGHT = 160;

interface SlidesState {
  open: boolean;
  deckBySession: Record<number, DeckEventData | undefined>;
  /** Monotonic counter bumped on every `setDeck` (i.e. every `deck` SSE event /
   *  recompile). The Slides panel keys its PDF fetch on this so a completed
   *  edit forces a cache-busted refetch of the freshly compiled deck. */
  deckRevisionBySession: Record<number, number>;
  currentPageBySession: Record<number, number>;
  /** Draggable filmstrip rail width (px). Persisted. */
  filmstripWidth: number;
  /** Draggable speaker-note pane height (px). Persisted. */
  noteHeight: number;
  setDeck: (sid: number, deck: DeckEventData) => void;
  clearDeck: (sid: number) => void;
  setCurrentPage: (sid: number, page: number) => void;
  toggleOpen: () => void;
  openPanel: () => void;
  closePanel: () => void;
  setFilmstripWidth: (width: number) => void;
  setNoteHeight: (height: number) => void;
}

export const useSlidesStore = create<SlidesState>()(
  persist(
    (set) => ({
      open: false,
      deckBySession: {},
      deckRevisionBySession: {},
      currentPageBySession: {},
      filmstripWidth: FILMSTRIP_DEFAULT_WIDTH,
      noteHeight: NOTE_DEFAULT_HEIGHT,
      setDeck: (sid, deck) =>
        set((s) => ({
          deckBySession: { ...s.deckBySession, [sid]: deck },
          deckRevisionBySession: {
            ...s.deckRevisionBySession,
            [sid]: (s.deckRevisionBySession[sid] ?? 0) + 1,
          },
        })),
      clearDeck: (sid) =>
        set((s) => {
          if (s.deckBySession[sid] === undefined) return s;
          const next = { ...s.deckBySession };
          delete next[sid];
          return { deckBySession: next };
        }),
      setCurrentPage: (sid, page) =>
        set((s) => ({
          currentPageBySession: { ...s.currentPageBySession, [sid]: page },
        })),
      toggleOpen: () => set((s) => ({ open: !s.open })),
      openPanel: () => set({ open: true }),
      closePanel: () => set({ open: false }),
      setFilmstripWidth: (width) => set({ filmstripWidth: width }),
      setNoteHeight: (height) => set({ noteHeight: height }),
    }),
    {
      name: "paperhub-slides-v1",
      storage: createJSONStorage(() => localStorage),
      // Persist ONLY the layout dimensions — deck data + open state are
      // ephemeral (re-fetched / re-opened per session, not restored on reload).
      partialize: (s) => ({
        filmstripWidth: s.filmstripWidth,
        noteHeight: s.noteHeight,
      }),
    },
  ),
);
