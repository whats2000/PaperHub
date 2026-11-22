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
/** A paper this chat shows but does not have among its enabled papers (a
 *  `[[` link, a citation to a switched-off paper, a library paper): its
 *  dashed tab in both the reader and the notes pane. `preview`: opened by
 *  looking (italic), replaced by the next one; editing its note or a double
 *  click keeps it. */
export interface OutsideTab {
  paperId: number;
  preview: boolean;
}

/** A dashed tab closed in either pane: both panes showing it land on
 *  `neighbour` (null: the chat's first paper). */
export interface ClosedTab {
  chat: number;
  paperId: number;
  neighbour: number | null;
  n: number;
}

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
  /** The last paper request came from the notes pane's tabs (selectPaper):
   *  the reader follows without coming forward over Notes. */
  paperRequestQuiet: boolean;
  /** The paper currently SHOWN on the canvas (by paper_content_id), or null when
   *  the canvas is closed / empty. Published by CitationCanvas (it owns the
   *  displayed-paper state) so other UI — the References panel — can mark which
   *  paper is live. Ephemeral (not persisted). */
  activePaperId: number | null;
  /** The section heading the reader is at, per the reader's scroll (HTML
   *  papers); the notes pane's Follow scrolls the note to it. Ephemeral. */
  readingSection: ReadingSection | null;
  /** How the reader shows its paper (the HTML or a PDF: a PDF-only paper or
   *  the PDF tab), published by CitationCanvas; null when it shows none. */
  readerView: ReaderView | null;
  /** A request to show a paper in one of its views (an HTML paper with a
   *  page PDF: its HTML or PDF tab), e.g. the view a mark was made in. */
  readerTabRequest: { paperId: number; tab: "html" | "pdf"; n: number } | null;
  requestReaderTab: (paperId: number, tab: "html" | "pdf") => void;
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
  /** Switches the reader's paper without opening it (the notes pane's
   *  paper tabs: both panes show the same paper). */
  selectPaper: (paperContentId: number) => void;
  /** Per chat (its local id), its dashed tabs, in the order they opened.
   *  Kept for this page load only. */
  outsideByChat: Record<number, OutsideTab[]>;
  /** Shows a paper as a dashed tab: a temporary one, replacing the chat's
   *  temporary tab; a paper that has a tab keeps it. */
  showOutside: (chat: number, paperContentId: number) => void;
  /** The temporary tab stays (no longer replaced by the next one). */
  keepOutside: (chat: number, paperContentId: number) => void;
  /** Closes a dashed tab in both panes (see `closedTab`). */
  closeOutside: (chat: number, paperContentId: number, neighbour: number | null) => void;
  /** Forgets a dashed tab quietly (its paper was added to the chat). */
  forgetOutside: (chat: number, paperContentId: number) => void;
  /** The dashed tab closed last, for the panes that showed it. */
  closedTab: ClosedTab | null;
  /** A paper's title as the reader looked it up (a library paper this chat
   *  does not have), for the notes pane's tab. */
  paperTitles: Record<number, string>;
  setPaperTitle: (paperContentId: number, title: string) => void;
  /** Cleared by the canvas once it has switched to the requested paper, so a
   *  later browse-mode reopen doesn't re-jump to it. */
  consumePaperRequest: () => void;
  /** Set by CitationCanvas to publish the paper it is currently showing (null
   *  when closed/empty), so the References panel can highlight the active row. */
  setActivePaperId: (paperContentId: number | null) => void;
  /** Published by the reader as it scrolls (null heading: before the first). */
  /** `within`: the headings of the sections `heading` is in, outermost first. */
  setReadingSection: (paperContentId: number, heading: string | null, within?: string[]) => void;
  setReaderView: (view: ReaderView | null) => void;
  /** References button: open in browse mode if closed, else close. */
  toggleCanvas: () => void;
  closeCanvas: () => void;
  /** The chat changed: close the reader and forget the paper it showed
   *  (that paper belongs to the chat left behind); temporary tabs close. */
  resetForSessionChange: () => void;
  setWidth: (width: number) => void;
}

export interface ReadingSection {
  paperId: number;
  heading: string;
  /** The headings of the sections `heading` is in, outermost first. */
  within: string[];
}

export interface ReaderView {
  paperId: number;
  kind: "html" | "pdf";
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
      paperRequestQuiet: false,
      activePaperId: null,
      readingSection: null,
      readerView: null,
      readerTabRequest: null,
      requestReaderTab: (paperId, tab) =>
        set((s) => ({ readerTabRequest: { paperId, tab, n: (s.readerTabRequest?.n ?? 0) + 1 } })),
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
          paperRequestQuiet: false,
        })),
      selectPaper: (paperContentId) =>
        set((s) => ({
          requestedPaperId: paperContentId,
          paperRequestNonce: s.paperRequestNonce + 1,
          paperRequestQuiet: true,
        })),
      outsideByChat: {},
      showOutside: (chat, paperContentId) =>
        set((s) => {
          const tabs = s.outsideByChat[chat] ?? [];
          if (tabs.some((t) => t.paperId === paperContentId)) return {};
          const next = [...tabs.filter((t) => !t.preview), { paperId: paperContentId, preview: true }];
          return { outsideByChat: { ...s.outsideByChat, [chat]: next } };
        }),
      keepOutside: (chat, paperContentId) =>
        set((s) => {
          const tabs = s.outsideByChat[chat] ?? [];
          if (!tabs.some((t) => t.paperId === paperContentId && t.preview)) return {};
          const next = tabs.map((t) => (t.paperId === paperContentId ? { ...t, preview: false } : t));
          return { outsideByChat: { ...s.outsideByChat, [chat]: next } };
        }),
      closeOutside: (chat, paperContentId, neighbour) =>
        set((s) => ({
          outsideByChat: {
            ...s.outsideByChat,
            [chat]: (s.outsideByChat[chat] ?? []).filter((t) => t.paperId !== paperContentId),
          },
          closedTab: { chat, paperId: paperContentId, neighbour, n: (s.closedTab?.n ?? 0) + 1 },
        })),
      forgetOutside: (chat, paperContentId) =>
        set((s) =>
          (s.outsideByChat[chat] ?? []).some((t) => t.paperId === paperContentId)
            ? {
                outsideByChat: {
                  ...s.outsideByChat,
                  [chat]: (s.outsideByChat[chat] ?? []).filter((t) => t.paperId !== paperContentId),
                },
              }
            : {},
        ),
      closedTab: null,
      paperTitles: {},
      setPaperTitle: (paperContentId, title) =>
        set((s) =>
          s.paperTitles[paperContentId] === title
            ? {}
            : { paperTitles: { ...s.paperTitles, [paperContentId]: title } },
        ),
      consumePaperRequest: () => set({ requestedPaperId: null }),
      setActivePaperId: (paperContentId) =>
        set({ activePaperId: paperContentId }),
      setReadingSection: (paperId, heading, within = []) =>
        set((s) => {
          const cur = s.readingSection;
          if (heading === null) return cur?.paperId === paperId ? { readingSection: null } : {};
          const same =
            cur?.paperId === paperId && cur.heading === heading && JSON.stringify(cur.within) === JSON.stringify(within);
          return same ? {} : { readingSection: { paperId, heading, within } };
        }),
      setReaderView: (view) =>
        set((s) =>
          s.readerView?.paperId === view?.paperId && s.readerView?.kind === view?.kind
            ? {}
            : { readerView: view },
        ),
      toggleCanvas: () => set((s) => ({ open: !s.open })),
      closeCanvas: () => set({ open: false }),
      resetForSessionChange: () =>
        set((s) => ({
          open: false,
          activePaperId: null,
          readerView: null,
          readingSection: null,
          outsideByChat: Object.fromEntries(
            Object.entries(s.outsideByChat).map(([chat, tabs]) => [chat, tabs.filter((t) => !t.preview)]),
          ),
        })),
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
