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
  /** Per-session "version-restore in flight" flag. The DeckChip's Switch
   *  affordance flips this true around the restore POST + getDeck round-trip;
   *  the SlidesPanel folds it into its mask so the old PDF doesn't sit on
   *  screen pretending it's the restored one. Mirrors how the chat-turn
   *  ``busy`` prop masks during an edit. */
  restoringBySession: Record<number, boolean>;
  currentPageBySession: Record<number, number>;
  /** Per-session "presentation mode active" flag. Ephemeral (NOT persisted) and
   *  kept in the store — never in SlidesPanel local state — so the Q&A
   *  close/reopen (a panel unmount/remount) does not lose it. */
  presentingBySession: Record<number, boolean>;
  /** Epoch ms when presentation began, per session — drives the cockpit timer
   *  so it survives a panel remount during Q&A. */
  presentStartedAtBySession: Record<number, number>;
  /** Draggable filmstrip rail width (px). Persisted. */
  filmstripWidth: number;
  /** Draggable speaker-note pane height (px). Persisted. */
  noteHeight: number;
  setDeck: (sid: number, deck: DeckEventData) => void;
  clearDeck: (sid: number) => void;
  setCurrentPage: (sid: number, page: number) => void;
  setRestoring: (sid: number, restoring: boolean) => void;
  startPresenting: (sid: number) => void;
  stopPresenting: (sid: number) => void;
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
      restoringBySession: {},
      currentPageBySession: {},
      presentingBySession: {},
      presentStartedAtBySession: {},
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
      setRestoring: (sid, restoring) =>
        set((s) => ({
          restoringBySession: { ...s.restoringBySession, [sid]: restoring },
        })),
      startPresenting: (sid) =>
        set((s) => ({
          presentingBySession: { ...s.presentingBySession, [sid]: true },
          presentStartedAtBySession: {
            ...s.presentStartedAtBySession,
            [sid]: Date.now(),
          },
        })),
      stopPresenting: (sid) =>
        set((s) => ({
          presentingBySession: { ...s.presentingBySession, [sid]: false },
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
