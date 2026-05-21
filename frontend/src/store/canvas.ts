import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/** Canvas width bounds (px). */
export const CANVAS_MIN_WIDTH = 320;
export const CANVAS_DEFAULT_WIDTH = 480;

interface CanvasState {
  open: boolean;
  /** The chunk the user clicked a citation for. Null when opened via the
   *  References button (browse mode). The component resolves it → paper. */
  requestedChunkId: number | null;
  /** Bumped on every openCitation so clicking the SAME chunk twice re-triggers
   *  resolution in the component (which keys an effect on this). */
  requestNonce: number;
  /** User-adjustable panel width (px), set by dragging the divider. Persisted. */
  width: number;
  openCitation: (chunkId: number) => void;
  /** Cleared by the canvas once it has resolved the request, so re-opening in
   *  browse mode (References button — no new request) doesn't re-jump to the
   *  last-cited passage even when the canvas remounts. */
  consumeCitation: () => void;
  /** References button: open in browse mode if closed, else close. */
  toggleCanvas: () => void;
  closeCanvas: () => void;
  setWidth: (width: number) => void;
}

export const useCanvasStore = create<CanvasState>()(
  persist(
    (set) => ({
      open: false,
      requestedChunkId: null,
      requestNonce: 0,
      width: CANVAS_DEFAULT_WIDTH,
      openCitation: (chunkId) =>
        set((s) => ({
          open: true,
          requestedChunkId: chunkId,
          requestNonce: s.requestNonce + 1,
        })),
      consumeCitation: () => set({ requestedChunkId: null }),
      toggleCanvas: () => set((s) => ({ open: !s.open })),
      closeCanvas: () => set({ open: false }),
      setWidth: (width) => set({ width }),
    }),
    {
      name: "paperhub-canvas-v1",
      storage: createJSONStorage(() => localStorage),
      // Persist ONLY the width — `open` etc. are ephemeral (the canvas should
      // not auto-reopen on reload).
      partialize: (s) => ({ width: s.width }),
    },
  ),
);
