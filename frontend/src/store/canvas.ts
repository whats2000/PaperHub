import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/** Canvas width bounds (px). */
export const CANVAS_MIN_WIDTH = 320;
export const CANVAS_DEFAULT_WIDTH = 480;

/** A `[fig:P/ID]` citation: open the reader on that figure. */
export interface FigureRequest {
  paper: number;
  figureId: string;
}

/** A `[view:P/N/box]` citation: a region of a page the agent viewed — 1-based
 *  page, box `[x0, y0, x1, y1]` in 0-1000 page coordinates. */
export interface ViewRequest {
  paper: number;
  page: number;
  box: [number, number, number, number];
}

interface CanvasState {
  open: boolean;
  /** The chunk the user clicked a citation for. Null when opened via the
   *  References button (browse mode). The component resolves it → paper. */
  requestedChunkId: number | null;
  /** Optional END chunk for a MULTI-chunk citation (a slide Sources chip cites
   *  a whole section → highlight from `requestedChunkId` to this one). Null for
   *  a single-chunk `[chunk:N]` citation. */
  requestedEndChunkId: number | null;
  /** A figure citation to open (exclusive with the chunk / view requests). */
  requestedFigure: FigureRequest | null;
  /** A page-region citation to open (exclusive with the chunk / figure ones). */
  requestedView: ViewRequest | null;
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
  /** A paper the user asked to OPEN (References panel "Open in canvas" button) —
   *  by paper_content_id. The canvas switches its active tab to it. Distinct
   *  from a citation: no chunk highlight, just a paper swap. Null when none
   *  pending. */
  requestedPaperId: number | null;
  /** Bumped on every openPaper so clicking the SAME paper twice re-triggers the
   *  swap effect in the component (which keys an effect on this). */
  paperRequestNonce: number;
  /** The paper currently SHOWN on the canvas (by paper_content_id), or null when
   *  the canvas is closed / empty. Published by CitationCanvas (it owns the
   *  displayed-paper state) so other UI — the References panel — can mark which
   *  paper is live. Ephemeral (not persisted). */
  activePaperId: number | null;
  /** User-adjustable panel width (px), set by dragging the divider. Persisted. */
  width: number;
  openCitation: (chunkId: number, endChunkId?: number) => void;
  /** A figure citation: open the canvas on the figure and highlight it. */
  openFigure: (paper: number, figureId: string) => void;
  /** A page-region citation: open the canvas on the page and highlight it. */
  openView: (paper: number, page: number, box: ViewRequest["box"]) => void;
  /** Cleared by the canvas once it has resolved the request, so re-opening in
   *  browse mode (References button — no new request) doesn't re-jump to the
   *  last-cited passage even when the canvas remounts. */
  consumeCitation: () => void;
  /** References panel "Open in canvas": open the canvas (if closed) and switch
   *  its active paper to `paperContentId`. */
  openPaper: (paperContentId: number) => void;
  /** Cleared by the canvas once it has switched to the requested paper, so a
   *  later browse-mode reopen doesn't re-jump to it. */
  consumePaperRequest: () => void;
  /** Set by CitationCanvas to publish the paper it is currently showing (null
   *  when closed/empty), so the References panel can highlight the active row. */
  setActivePaperId: (paperContentId: number | null) => void;
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
      requestedEndChunkId: null,
      requestedFigure: null,
      requestedView: null,
      requestNonce: 0,
      requestAnimateScroll: false,
      requestedPaperId: null,
      paperRequestNonce: 0,
      activePaperId: null,
      width: CANVAS_DEFAULT_WIDTH,
      openCitation: (chunkId, endChunkId) =>
        set((s) => ({
          // Animate only when the canvas was ALREADY open (see the field doc).
          requestAnimateScroll: s.open,
          open: true,
          requestedChunkId: chunkId,
          requestedEndChunkId: endChunkId ?? null,
          requestedFigure: null,
          requestedView: null,
          requestNonce: s.requestNonce + 1,
        })),
      openFigure: (paper, figureId) =>
        set((s) => ({
          requestAnimateScroll: s.open,
          open: true,
          requestedChunkId: null,
          requestedEndChunkId: null,
          requestedFigure: { paper, figureId },
          requestedView: null,
          requestNonce: s.requestNonce + 1,
        })),
      openView: (paper, page, box) =>
        set((s) => ({
          requestAnimateScroll: s.open,
          open: true,
          requestedChunkId: null,
          requestedEndChunkId: null,
          requestedFigure: null,
          requestedView: { paper, page, box },
          requestNonce: s.requestNonce + 1,
        })),
      consumeCitation: () =>
        set({
          requestedChunkId: null,
          requestedEndChunkId: null,
          requestedFigure: null,
          requestedView: null,
        }),
      openPaper: (paperContentId) =>
        set((s) => ({
          open: true,
          requestedPaperId: paperContentId,
          paperRequestNonce: s.paperRequestNonce + 1,
        })),
      consumePaperRequest: () => set({ requestedPaperId: null }),
      setActivePaperId: (paperContentId) =>
        set({ activePaperId: paperContentId }),
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
