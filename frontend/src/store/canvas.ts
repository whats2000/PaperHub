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
  /** Whether the citation scroll should animate. FALSE when the click also
   *  OPENED the canvas (the iframe lays out for the first time while the panel
   *  animates open + content-visibility blocks render, so a smooth glide would
   *  track a shifting target and land wrong — an instant jump is acceptable).
   *  TRUE when the canvas was already open: the glide shows the reader where the
   *  passage sits relative to the current view. */
  requestAnimateScroll: boolean;
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
      requestAnimateScroll: false,
      width: CANVAS_DEFAULT_WIDTH,
      openCitation: (chunkId) =>
        set((s) => ({
          // Animate only when the canvas was ALREADY open (see the field doc).
          requestAnimateScroll: s.open,
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
