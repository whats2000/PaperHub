import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { DeckEventData, DeckSlideDetail } from "@/types/domain";

/** Manual-editor mode for a session's deck (F6.2): off / editing the current
 *  frame / editing the whole deck source. Mutually exclusive. */
export type EditorMode = "off" | "frame" | "deck";

/** Filmstrip rail width bounds (px). */
export const FILMSTRIP_MIN_WIDTH = 56;
export const FILMSTRIP_MAX_WIDTH = 280;
export const FILMSTRIP_DEFAULT_WIDTH = 80;

/** Speaker note pane height bounds (px). */
export const NOTE_MIN_HEIGHT = 80;
export const NOTE_DEFAULT_HEIGHT = 160;

interface SlidesState {
  open: boolean;
  /** True once the Slides panel has been opened at least once. ChatPage keeps
   *  the panel MOUNTED (hidden) thereafter so a Canvas↔Slides swap doesn't
   *  unmount it (which would refetch + re-rasterize the PDF — the reload flash).
   *  Ephemeral (resets on reload). */
  everOpened: boolean;
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
  /** Per-session "attach the on-screen slide as chat context" toggle (the
   *  composer chip's eye). Sticky per session: persists across slide changes;
   *  the attached CONTENT tracks the active slide via currentPageBySession.
   *  Undefined → attached (auto-on when a deck is open). Ephemeral (not
   *  persisted), like deck data. */
  slideAttachedBySession: Record<number, boolean>;
  setSlideAttached: (sid: number, attached: boolean) => void;
  /** Per-session "presentation mode active" flag. Ephemeral (NOT persisted) and
   *  kept in the store — never in SlidesPanel local state — so the Q&A
   *  close/reopen (a panel unmount/remount) does not lose it. */
  presentingBySession: Record<number, boolean>;
  /** Epoch ms when presentation began, per session — drives the cockpit timer
   *  so it survives a panel remount during Q&A. `stopPresenting` deliberately
   *  leaves the last value here (harmless: it is only read while
   *  `presentingBySession[sid]` is true, and `startPresenting` re-stamps it on
   *  every start), so consumers MUST gate on the presenting flag first. */
  presentStartedAtBySession: Record<number, number>;
  /** Per-session manual-editor mode (F6.2). Ephemeral (not persisted) — closing
   *  the panel exits the editor. */
  editorModeBySession: Record<number, EditorMode>;
  setEditorMode: (sid: number, mode: EditorMode) => void;
  /** Per-session per-slide detail (frame source + grounding) for the editor +
   *  Sources strip. Ephemeral; refetched on deck load / revision bump. */
  slidesSourcesBySession: Record<number, DeckSlideDetail[]>;
  setSlidesSources: (sid: number, slides: DeckSlideDetail[]) => void;
  /** Bump a session's deck revision WITHOUT replacing deck metadata — used
   *  after a manual recompile to force a cache-busted PDF refetch (the SSE
   *  ``deck`` event path uses ``setDeck`` instead). */
  bumpDeckRevision: (sid: number) => void;
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
      everOpened: false,
      deckBySession: {},
      deckRevisionBySession: {},
      restoringBySession: {},
      currentPageBySession: {},
      slideAttachedBySession: {},
      presentingBySession: {},
      presentStartedAtBySession: {},
      editorModeBySession: {},
      slidesSourcesBySession: {},
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
      setEditorMode: (sid, mode) =>
        set((s) => ({
          editorModeBySession: { ...s.editorModeBySession, [sid]: mode },
        })),
      setSlidesSources: (sid, slides) =>
        set((s) => ({
          slidesSourcesBySession: { ...s.slidesSourcesBySession, [sid]: slides },
        })),
      bumpDeckRevision: (sid) =>
        set((s) => ({
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
      setSlideAttached: (sid, attached) =>
        set((s) => ({
          slideAttachedBySession: { ...s.slideAttachedBySession, [sid]: attached },
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
      toggleOpen: () =>
        set((s) => ({ open: !s.open, everOpened: s.everOpened || !s.open })),
      openPanel: () => set({ open: true, everOpened: true }),
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
