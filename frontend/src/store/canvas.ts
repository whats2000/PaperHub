import { create } from "zustand";

interface CanvasState {
  open: boolean;
  /** The chunk the user clicked a citation for. Null when opened via the
   *  References button (browse mode). The component resolves it → paper. */
  requestedChunkId: number | null;
  /** Bumped on every openCitation so clicking the SAME chunk twice re-triggers
   *  resolution in the component (which keys an effect on this). */
  requestNonce: number;
  openCitation: (chunkId: number) => void;
  /** Cleared by the canvas once it has resolved the request, so re-opening in
   *  browse mode (References button — no new request) doesn't re-jump to the
   *  last-cited passage even when the canvas remounts. */
  consumeCitation: () => void;
  /** References button: open in browse mode if closed, else close. */
  toggleCanvas: () => void;
  closeCanvas: () => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  open: false,
  requestedChunkId: null,
  requestNonce: 0,
  openCitation: (chunkId) =>
    set((s) => ({ open: true, requestedChunkId: chunkId, requestNonce: s.requestNonce + 1 })),
  consumeCitation: () => set({ requestedChunkId: null }),
  toggleCanvas: () => set((s) => ({ open: !s.open })),
  closeCanvas: () => set({ open: false }),
}));
