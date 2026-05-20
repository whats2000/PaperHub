import { create } from "zustand";

interface CanvasState {
  open: boolean;
  /** The chunk whose passage we want to scroll to + highlight. */
  chunkId: number | null;
  openCitation: (chunkId: number) => void;
  closeCanvas: () => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  open: false,
  chunkId: null,
  openCitation: (chunkId) => set({ open: true, chunkId }),
  closeCanvas: () => set({ open: false }),
}));
