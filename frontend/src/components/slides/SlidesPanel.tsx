import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  Pencil,
  X,
} from "lucide-react";

import {
  useSlidesStore,
  FILMSTRIP_MIN_WIDTH,
  FILMSTRIP_MAX_WIDTH,
  NOTE_MIN_HEIGHT,
} from "@/store/slides";
import { fetchDeckPdfData, deckPdfUrl, deckTexUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";

// pdf.js needs a worker; resolve it from the installed pdfjs-dist via Vite's
// import.meta.url so the worker is bundled + served from the app origin.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

/** Horizontal chrome (rail padding + thumbnail border) subtracted from the
 *  rail width to get the thumbnail render width. Matches the 80→64 default. */
const FILMSTRIP_THUMB_INSET = 16;

interface Props {
  sessionId: number;
  speakerNotes: Record<string, string>;
  /** A slides generate/edit turn is in flight for this session. While true the
   *  canvas is masked and the PDF is NOT refetched (the recompile isn't ready);
   *  on completion the revision bump triggers a cache-busted reload. */
  busy?: boolean;
  /** Present-tense status shown on the mask (live slide-agent stage). */
  stage?: string;
  /** Persist a manual speaker-note edit for `page`. When omitted the note pane
   *  stays read-only (no Edit affordance). */
  onSaveNote?: (page: number, text: string) => Promise<void>;
}

/** Sentinel the backend writes on pages a multi-page slide spills onto; its
 *  real note lives on the slide's first page, so we don't offer to edit it. */
const CONTINUED_NOTE = "(continued)";

/**
 * SlidesPanel — renders the compiled deck PDF with:
 * - A left filmstrip rail of thumbnail pages (clickable, active page marked),
 *   with a draggable vertical divider to resize the rail.
 * - A main slide area showing the current page at container width.
 * - A header with title, prev/next navigation, page counter, and download links.
 * - A resizable speaker note pane below the slide (draggable horizontal divider).
 * - Keyboard navigation: ArrowLeft/ArrowRight change page.
 */
export function SlidesPanel({
  sessionId,
  speakerNotes,
  busy = false,
  stage,
  onSaveNote,
}: Props) {
  const deck = useSlidesStore((s) => s.deckBySession[sessionId]);
  const revision = useSlidesStore((s) => s.deckRevisionBySession[sessionId] ?? 0);
  const currentPage = useSlidesStore(
    (s) => s.currentPageBySession[sessionId] ?? 1,
  );
  const setCurrentPage = useSlidesStore((s) => s.setCurrentPage);
  // Layout dimensions live in the store so they survive panel close/reopen
  // (the panel unmounts on close) and reload (persisted to localStorage).
  const noteHeight = useSlidesStore((s) => s.noteHeight);
  const setNoteHeight = useSlidesStore((s) => s.setNoteHeight);
  const filmstripWidth = useSlidesStore((s) => s.filmstripWidth);
  const setFilmstripWidth = useSlidesStore((s) => s.setFilmstripWidth);

  // Currently-loaded PDF bytes + the (session, revision) they belong to. We
  // keep the last good bytes on screen while a newer revision of the SAME
  // session is fetching so an edit-complete reload swaps in under the mask
  // rather than flashing blank.
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [mainWidth, setMainWidth] = useState(0);
  // Defer the width used for pdf.js rasterisation so a rapid resize-drag
  // doesn't fire N renders per pixel across every mounted page. React holds
  // the previous value during high-priority renders and settles to the
  // latest when idle — single-pointer-event rasterise instead of per-pixel.
  const renderWidth = useDeferredValue(mainWidth);
  // Measured thumbnail render width — derived from the rail's actual inner
  // width (excludes the scrollbar), NOT the style width, so a thumbnail never
  // overflows + gets clipped on the right when the rail scrolls.
  const [thumbWidth, setThumbWidth] = useState(
    filmstripWidth - FILMSTRIP_THUMB_INSET,
  );

  const panelRef = useRef<HTMLDivElement>(null);
  const roRef = useRef<ResizeObserver | null>(null);
  const filmRoRef = useRef<ResizeObserver | null>(null);

  // Measure the main slide area via a CALLBACK ref so it fires the instant the
  // element mounts — which is when the <Document> renders (file != null), not
  // on first component mount. This avoids the page rendering at its intrinsic
  // ~362pt Beamer width and leaving the panel half-blank. A ResizeObserver
  // keeps the width correct through the panel open-animation + divider drags.
  const measureMainArea = useCallback((el: HTMLDivElement | null) => {
    roRef.current?.disconnect();
    roRef.current = null;
    if (!el) return;
    const measure = () => setMainWidth(Math.max(0, el.clientWidth - 16));
    measure();
    if (typeof ResizeObserver === "undefined") return; // jsdom guard
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    roRef.current = ro;
  }, []);

  // Measure the filmstrip rail's inner content width via a callback ref. We use
  // clientWidth (excludes border + scrollbar) minus padding (p-1 = 8px) and the
  // thumbnail button's border (border-2 = 4px), so the thumbnail always fits.
  const measureFilmstrip = useCallback((el: HTMLDivElement | null) => {
    filmRoRef.current?.disconnect();
    filmRoRef.current = null;
    if (!el) return;
    const measure = () => setThumbWidth(Math.max(32, el.clientWidth - 12));
    measure();
    if (typeof ResizeObserver === "undefined") return; // jsdom guard
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    filmRoRef.current = ro;
  }, []);

  // Disconnect any live observer on unmount.
  useEffect(
    () => () => {
      roRef.current?.disconnect();
      filmRoRef.current?.disconnect();
    },
    [],
  );

  // Identity of the bytes currently loaded, parsed from loadedKey.
  const [loadedSid, loadedRev] = loadedKey
    ? loadedKey.split(":").map(Number)
    : [null, null];

  // Fetch PDF bytes for the current (session, revision). Gated on !busy: while a
  // generate/edit turn is streaming the recompiled PDF isn't ready, so we hold
  // the current bytes under the mask and don't refetch. When the turn finishes
  // the revision has bumped (setDeck) and busy clears → this refetches the fresh
  // deck (cache-busted by revision). State is set only in the async callback, so
  // the effect never updates state synchronously.
  const fetchKey = `${sessionId}:${revision}`;
  useEffect(() => {
    if (busy) return; // mid-edit: keep current bytes, mask covers the panel
    if (loadedKey === fetchKey) return; // already showing this revision
    let cancelled = false;
    fetchDeckPdfData(sessionId, revision)
      .then((b) => {
        if (cancelled) return;
        setBytes(b);
        setLoadedKey(fetchKey);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, revision, busy, fetchKey, loadedKey]);

  // pdfjs TRANSFERS (detaches) the ArrayBuffer it's given to its worker, so
  // pass a fresh COPY each time (same pattern as PdfView.tsx). Only render the
  // bytes if they belong to THIS session (on a session switch the old session's
  // bytes are ignored until the new ones load). Memoized so the Document
  // doesn't reload on unrelated re-renders.
  const file = useMemo(
    () => (bytes && loadedSid === sessionId ? { data: bytes.slice() } : null),
    [bytes, loadedSid, sessionId],
  );

  // Mask the canvas while a turn is in flight, OR while a completed edit's fresh
  // deck is still loading (same session, newer revision than what's on screen).
  // Derived — no extra state — so the effect stays setState-free.
  const reloading =
    !busy && bytes !== null && loadedSid === sessionId && loadedRev !== revision;
  const masked = busy || reloading;

  // Keyboard navigation.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") {
        setCurrentPage(sessionId, Math.max(1, currentPage - 1));
      } else if (e.key === "ArrowRight") {
        setCurrentPage(sessionId, Math.min(numPages || 1, currentPage + 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sessionId, currentPage, numPages, setCurrentPage]);

  // Draggable vertical divider — mirrors useCanvasResize but on the Y axis.
  const onDividerPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startY = e.clientY;
      const startHeight = noteHeight;

      const onMove = (ev: PointerEvent) => {
        const panel = panelRef.current;
        const maxH = panel ? Math.round(panel.clientHeight * 0.6) : 400;
        // Drag down → smaller note; drag up → larger note.
        const next = Math.min(
          Math.max(startHeight + (startY - ev.clientY), NOTE_MIN_HEIGHT),
          maxH,
        );
        setNoteHeight(next);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [noteHeight, setNoteHeight],
  );

  // Draggable vertical divider between the filmstrip rail and the slide area.
  // Drag right → wider filmstrip; clamped to [MIN, MAX].
  const onFilmstripDividerPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = filmstripWidth;

      const onMove = (ev: PointerEvent) => {
        const next = Math.min(
          Math.max(startWidth + (ev.clientX - startX), FILMSTRIP_MIN_WIDTH),
          FILMSTRIP_MAX_WIDTH,
        );
        setFilmstripWidth(next);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [filmstripWidth, setFilmstripWidth],
  );

  const goTo = (page: number) => {
    const clamped = Math.min(Math.max(page, 1), numPages || 1);
    setCurrentPage(sessionId, clamped);
  };

  const speakerNote = speakerNotes[String(currentPage)];

  // --- manual speaker-note editing ---
  // Track WHICH page is being edited (not a bare bool) so navigating to another
  // page auto-exits edit mode by derivation — no setState-in-effect needed.
  const [editingPage, setEditingPage] = useState<number | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const editingNote = editingPage === currentPage;
  // A continuation page ("(continued)") and an in-flight deck edit both block
  // editing; the real note lives on the owning slide's first page.
  const noteEditable =
    !!onSaveNote && !busy && speakerNote !== CONTINUED_NOTE;

  const beginEditNote = () => {
    setNoteDraft(speakerNote && speakerNote !== CONTINUED_NOTE ? speakerNote : "");
    setEditingPage(currentPage);
  };
  const cancelEditNote = () => setEditingPage(null);
  const saveNote = async () => {
    if (!onSaveNote) return;
    setSavingNote(true);
    try {
      await onSaveNote(currentPage, noteDraft);
      setEditingPage(null);
    } finally {
      setSavingNote(false);
    }
  };

  return (
    <div
      ref={panelRef}
      className="flex flex-col h-full bg-card text-foreground overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
        <span className="text-xs font-semibold truncate flex-1 text-foreground">
          {deck?.title ?? "Slides"}
        </span>

        {/* Status indicator */}
        {deck?.status === "ok" ? (
          <span className="text-xs text-green-600 dark:text-green-400 shrink-0">
            ready
          </span>
        ) : deck?.status === "error" ? (
          <span className="text-xs text-destructive shrink-0">error</span>
        ) : null}

        {/* Navigation */}
        <Button
          type="button"
          size="icon-xs"
          variant="ghost"
          aria-label="previous slide"
          disabled={currentPage <= 1}
          onClick={() => goTo(currentPage - 1)}
        >
          <ChevronLeft className="h-3 w-3" />
        </Button>
        <span className="text-xs text-muted-foreground tabular-nums shrink-0">
          {currentPage} / {numPages || deck?.page_count || "–"}
        </span>
        <Button
          type="button"
          size="icon-xs"
          variant="ghost"
          aria-label="next slide"
          disabled={numPages > 0 && currentPage >= numPages}
          onClick={() => goTo(currentPage + 1)}
        >
          <ChevronRight className="h-3 w-3" />
        </Button>

        {/* Download links */}
        <a
          href={deckPdfUrl(sessionId)}
          download
          aria-label="Download PDF"
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <Download className="h-3 w-3" />
        </a>
        <a
          href={deckTexUrl(sessionId)}
          download
          aria-label="Download LaTeX source"
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          .tex
        </a>
      </div>

      {/* Body: single Document wraps filmstrip rail + main slide area.
          react-pdf shares one parsed PDF across all child <Page> components via
          context — two Documents would transfer (detach) the ArrayBuffer to the
          pdfjs worker on the first mount, leaving the second with a zero-length
          buffer and a blank render. Wrapped in a relative container so the
          editing mask can overlay the whole slide region. */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">
      {file ? (
        <Document
          file={file}
          onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
          className="flex flex-1 min-h-0 overflow-hidden"
        >
          {/* Left filmstrip rail */}
          <div
            ref={measureFilmstrip}
            className="flex flex-col gap-1 p-1 overflow-y-auto bg-muted/30 shrink-0"
            style={{ width: filmstripWidth }}
          >
            {Array.from(
              { length: numPages || deck?.page_count || 0 },
              (_, i) => {
                const pageNum = i + 1;
                const isActive = pageNum === currentPage;
                return (
                  <button
                    key={pageNum}
                    type="button"
                    aria-label={`slide ${pageNum}`}
                    onClick={() => goTo(pageNum)}
                    className={`w-full rounded overflow-hidden border-2 transition-colors shrink-0 ${
                      isActive
                        ? "border-primary"
                        : "border-transparent hover:border-border"
                    }`}
                  >
                    <Page pageNumber={pageNum} width={thumbWidth} />
                    <span className="block text-center text-xs text-muted-foreground py-0.5">
                      {pageNum}
                    </span>
                  </button>
                );
              },
            )}
          </div>

          {/* Draggable vertical divider between filmstrip + slide area */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="resize filmstrip"
            onPointerDown={onFilmstripDividerPointerDown}
            className="w-1.5 cursor-col-resize bg-border hover:bg-primary/40 transition-colors shrink-0"
          />

          {/* Main content column: ALL pages mounted, only the current one
              visible (display:none on the rest). pdf.js rasterises each <Page>
              exactly once on mount and keeps the rendered canvas in the DOM —
              page-switching becomes a CSS class toggle, with zero re-render
              (the live-preview snappiness this UX needs). Memory cost is
              bounded by the deck budget (8–30 slides) at ~2 MB per rasterised
              canvas. Width drives all pages, but is deferred (useDeferredValue)
              so a resize-drag doesn't rasterise N pages per pixel. */}
          <div
            ref={measureMainArea}
            className="flex-1 min-h-0 overflow-auto bg-neutral-100 dark:bg-neutral-900 p-2"
          >
            {Array.from({ length: numPages }, (_, i) => {
              const pageNum = i + 1;
              const isActive = pageNum === currentPage;
              return (
                <div
                  key={pageNum}
                  // `hidden` (display:none) keeps the page in the DOM (canvas
                  // stays rendered) but takes it out of the flow — no scroll,
                  // no overlap, no interaction.
                  hidden={!isActive}
                  aria-hidden={!isActive}
                  data-page={pageNum}
                >
                  <Page
                    pageNumber={pageNum}
                    width={renderWidth > 0 ? renderWidth : undefined}
                    className="mx-auto shadow"
                  />
                </div>
              );
            })}
          </div>
        </Document>
      ) : (
        <div className="flex flex-1 min-h-0 overflow-hidden" />
      )}

        {/* Editing mask — covers the slide region while a generate/edit turn is
            in flight or the post-edit reload is fetching. The current deck stays
            underneath (non-interactive) so the swap-in is seamless. */}
        {masked && (
          <div
            role="status"
            aria-live="polite"
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-card/70 backdrop-blur-sm"
          >
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">
              Updating slides…
            </span>
            {stage && (
              <span className="px-4 text-center text-xs text-muted-foreground">
                {stage}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Draggable divider (outside Document — no pdfjs dependency) */}
      <div
        role="separator"
        aria-orientation="horizontal"
        onPointerDown={onDividerPointerDown}
        className="h-1.5 cursor-row-resize bg-border hover:bg-primary/40 transition-colors shrink-0"
      />

      {/* Speaker note pane (outside Document — no pdfjs dependency). Fixed-height
          flex column: the header stays put and the body (text or textarea) fills
          the rest and scrolls internally, so entering edit mode never grows the
          pane (a flex item's min-height:auto would otherwise stretch it to the
          textarea's intrinsic row height). */}
      <div
        className="shrink-0 flex flex-col min-h-0 border-t border-border bg-muted/20 px-3 py-2"
        style={{ height: noteHeight }}
      >
        <div className="mb-1 flex items-center gap-1 shrink-0">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Speaker note
          </p>
          {/* Edit / Save+Cancel controls live in the header (always visible) so
              a tall note textarea never pushes them out of the scroll area. */}
          {editingNote ? (
            <div className="ml-auto flex items-center gap-1">
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                aria-label="cancel note edit"
                onClick={cancelEditNote}
                disabled={savingNote}
              >
                <X className="h-3 w-3" />
              </Button>
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                aria-label="save speaker note"
                className="text-primary"
                onClick={() => void saveNote()}
                disabled={savingNote}
              >
                {savingNote ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Check className="h-3 w-3" />
                )}
              </Button>
            </div>
          ) : (
            noteEditable && (
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                aria-label="edit speaker note"
                className="ml-auto"
                onClick={beginEditNote}
              >
                <Pencil className="h-3 w-3" />
              </Button>
            )
          )}
        </div>

        {editingNote ? (
          <textarea
            aria-label="speaker note"
            className="flex-1 min-h-0 w-full resize-none rounded border border-border bg-background p-2 text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary"
            value={noteDraft}
            onChange={(e) => setNoteDraft(e.target.value)}
            disabled={savingNote}
            autoFocus
          />
        ) : speakerNote ? (
          <p className="flex-1 min-h-0 overflow-y-auto text-xs leading-relaxed whitespace-pre-wrap">
            {speakerNote}
          </p>
        ) : (
          <p className="flex-1 min-h-0 text-xs text-muted-foreground italic">
            No speaker note for this slide
          </p>
        )}
      </div>
    </div>
  );
}
