import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { ChevronLeft, ChevronRight, Download } from "lucide-react";

import { useSlidesStore } from "@/store/slides";
import { fetchDeckPdfData, deckPdfUrl, deckTexUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";

// pdf.js needs a worker; resolve it from the installed pdfjs-dist via Vite's
// import.meta.url so the worker is bundled + served from the app origin.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

/** Minimum height for the speaker note pane (px). */
const NOTE_MIN_HEIGHT = 80;
/** Initial height for the speaker note pane (px). */
const NOTE_DEFAULT_HEIGHT = 160;

interface Props {
  sessionId: number;
  speakerNotes: Record<string, string>;
}

/**
 * SlidesPanel — renders the compiled deck PDF with:
 * - A left filmstrip rail of thumbnail pages (clickable, active page marked).
 * - A main slide area showing the current page at container width.
 * - A header with title, prev/next navigation, page counter, and download links.
 * - A resizable speaker note pane below the slide (draggable horizontal divider).
 * - Keyboard navigation: ArrowLeft/ArrowRight change page.
 */
export function SlidesPanel({ sessionId, speakerNotes }: Props) {
  const deck = useSlidesStore((s) => s.deckBySession[sessionId]);
  const currentPage = useSlidesStore(
    (s) => s.currentPageBySession[sessionId] ?? 1,
  );
  const setCurrentPage = useSlidesStore((s) => s.setCurrentPage);

  // PDF bytes cache: keyed by sessionId so re-renders don't refetch.
  const [pdfCache, setPdfCache] = useState<Record<number, Uint8Array>>({});
  const [numPages, setNumPages] = useState(0);
  const [mainWidth, setMainWidth] = useState(0);
  const [noteHeight, setNoteHeight] = useState(NOTE_DEFAULT_HEIGHT);

  const panelRef = useRef<HTMLDivElement>(null);
  const roRef = useRef<ResizeObserver | null>(null);

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

  // Disconnect any live observer on unmount.
  useEffect(() => () => roRef.current?.disconnect(), []);

  // Fetch PDF bytes on mount / sessionId change; cache by sessionId.
  useEffect(() => {
    if (pdfCache[sessionId]) return; // already cached
    let cancelled = false;
    void fetchDeckPdfData(sessionId).then((bytes) => {
      if (!cancelled) {
        setPdfCache((prev) => ({ ...prev, [sessionId]: bytes }));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, pdfCache]);

  // pdfjs TRANSFERS (detaches) the ArrayBuffer it's given to its worker, so
  // pass a fresh COPY each time (same pattern as PdfView.tsx). Memoize so the
  // Document doesn't reload on unrelated re-renders.
  const rawBytes = pdfCache[sessionId];
  const file = useMemo(
    () => (rawBytes ? { data: rawBytes.slice() } : null),
    [rawBytes],
  );

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
    [noteHeight],
  );

  const goTo = (page: number) => {
    const clamped = Math.min(Math.max(page, 1), numPages || 1);
    setCurrentPage(sessionId, clamped);
  };

  const speakerNote = speakerNotes[String(currentPage)];

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
          buffer and a blank render. */}
      {file ? (
        <Document
          file={file}
          onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
          className="flex flex-1 min-h-0 overflow-hidden"
        >
          {/* Left filmstrip rail */}
          <div className="flex flex-col gap-1 p-1 overflow-y-auto border-r border-border bg-muted/30 shrink-0 w-[80px]">
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
                    <Page pageNumber={pageNum} width={64} />
                    <span className="block text-center text-xs text-muted-foreground py-0.5">
                      {pageNum}
                    </span>
                  </button>
                );
              },
            )}
          </div>

          {/* Main content column: current slide */}
          <div
            ref={measureMainArea}
            className="flex-1 min-h-0 overflow-auto bg-neutral-100 dark:bg-neutral-900 p-2"
          >
            <Page
              pageNumber={currentPage}
              width={mainWidth > 0 ? mainWidth : undefined}
              className="mx-auto shadow"
            />
          </div>
        </Document>
      ) : (
        <div className="flex flex-1 min-h-0 overflow-hidden" />
      )}

      {/* Draggable divider (outside Document — no pdfjs dependency) */}
      <div
        role="separator"
        aria-orientation="horizontal"
        onPointerDown={onDividerPointerDown}
        className="h-1.5 cursor-row-resize bg-border hover:bg-primary/40 transition-colors shrink-0"
      />

      {/* Speaker note pane (outside Document — no pdfjs dependency) */}
      <div
        className="shrink-0 overflow-y-auto border-t border-border bg-muted/20 px-3 py-2"
        style={{ height: noteHeight }}
      >
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
          Speaker note
        </p>
        {speakerNote ? (
          <p className="text-xs leading-relaxed">{speakerNote}</p>
        ) : (
          <p className="text-xs text-muted-foreground italic">
            No speaker note for this slide
          </p>
        )}
      </div>
    </div>
  );
}
