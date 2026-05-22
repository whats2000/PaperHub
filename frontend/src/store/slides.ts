import { create } from "zustand";
import type { DeckEventData } from "@/types/domain";

interface SlidesState {
  open: boolean;
  deckBySession: Record<number, DeckEventData | undefined>;
  currentPageBySession: Record<number, number>;
  setDeck: (sid: number, deck: DeckEventData) => void;
  setCurrentPage: (sid: number, page: number) => void;
  toggleOpen: () => void;
  openPanel: () => void;
  closePanel: () => void;
}

export const useSlidesStore = create<SlidesState>((set) => ({
  open: false,
  deckBySession: {},
  currentPageBySession: {},
  setDeck: (sid, deck) =>
    set((s) => ({ deckBySession: { ...s.deckBySession, [sid]: deck } })),
  setCurrentPage: (sid, page) =>
    set((s) => ({ currentPageBySession: { ...s.currentPageBySession, [sid]: page } })),
  toggleOpen: () => set((s) => ({ open: !s.open })),
  openPanel: () => set({ open: true }),
  closePanel: () => set({ open: false }),
}));
