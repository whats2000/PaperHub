import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { PaperLoading } from "@/components/canvas/PaperLoading";
import { locatePassage, type PdfPassageMatch } from "@/lib/pdfHighlight";
import {
  bboxToRect,
  hasGeometricRegion,
  markerPageToPageNumber,
} from "@/lib/bboxHighlight";

// pdf.js needs a worker; resolve it from the installed pdfjs-dist via Vite's
// import.meta.url so the worker is bundled + served from the app origin.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}
interface HighlightBoxes {
  pageNumber: number;
  rects: Rect[];
  /** Stable per cited passage, so the overlay remounts (→ scrolls) on a NEW
   *  citation but not on a resize-driven rect recompute. */
  key: string;
}

interface Props {
  /** Raw PDF bytes (fetched same-origin via the API). */
  data: Uint8Array;
  /** Cited passage to highlight + scroll to, when this PDF is the cited paper. */
  highlightText?: string | null;
  /** Marker chunk provenance (F2.1 A2'): the 0-based ABSOLUTE page index the
   *  cited chunk was extracted from. When non-null AND `bbox` is a valid
   *  length-4 region, the highlight is drawn GEOMETRICALLY from the bbox
   *  (exact — what the agent actually used) and the text-search path is
   *  skipped. Null → text-search fallback (`highlightText`). */
  bboxPage?: number | null;
  /** Marker union bbox `[x0,y0,x1,y1]` in PDF points, top-left origin, native
   *  page space. Paired with `bboxPage` for the geometric highlight path. */
  bbox?: number[] | null;
  /** Bumped per resolved citation so re-clicking the SAME chunk re-scrolls. */
  nonce?: number;
  /** Called when the passage couldn't be located in the PDF text. */
  onHighlightMiss?: () => void;
}

/**
 * Renders a PDF inline as scrollable canvas pages via react-pdf. The text layer
 * is rendered (so text is selectable), and the cited passage is highlighted by
 * drawing translucent boxes over the matched pdf.js text items — positioned
 * from the page's own viewport geometry (the same transform the canvas uses).
 *
 * We deliberately do NOT use react-pdf's `customTextRenderer`: it rewrites text-
 * layer spans by an index that drifts on marked-content (figure) pages,
 * mislocating the highlight and shifting the page's selectable text. A geometry
 * overlay is independent of the text layer, so it always aligns with the canvas.
 */
export function PdfView({
  data,
  highlightText,
  bboxPage,
  bbox,
  nonce = 0,
  onHighlightMiss,
}: Props) {
  // F2.1 A2': a non-null page + a well-formed length-4 bbox means we draw the
  // highlight exactly from the chunk's stored geometry (no text search).
  const useGeometric = hasGeometricRegion({ page: bboxPage, bbox });
  const [numPages, setNumPages] = useState(0);
  const [width, setWidth] = useState(0);
  const [match, setMatch] = useState<PdfPassageMatch | null>(null);
  const [highlight, setHighlight] = useState<HighlightBoxes | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const pdfRef = useRef<PDFDocumentProxy | null>(null);
  // Per-document text-item strings, built once and reused across passages.
  const pageTextRef = useRef<string[][] | null>(null);

  // pdfjs TRANSFERS (detaches) the ArrayBuffer it's given to its worker, so
  // passing the cached bytes directly would corrupt them and make a second
  // render fail. Give pdfjs a fresh COPY each mount; keep the cached original.
  const file = useMemo(() => ({ data: data.slice() }), [data]);

  // Fit pages to the container width (minus padding), tracking resize.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth - 24);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Locate the cited passage (which page + which text items) once the document
  // is loaded (numPages flips after onLoadSuccess) or when the passage changes.
  useEffect(() => {
    const pdf = pdfRef.current;
    // Geometric path owns the highlight when a bbox is present — skip text search.
    if (!pdf || !highlightText || useGeometric) {
      setMatch(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      if (!pageTextRef.current) {
        const pages: string[][] = [];
        for (let i = 1; i <= pdf.numPages; i++) {
          const page = await pdf.getPage(i);
          const text = await page.getTextContent();
          pages.push(text.items.map((it) => ("str" in it ? it.str : "")));
        }
        if (cancelled) return;
        pageTextRef.current = pages;
      }
      const found = locatePassage(pageTextRef.current, highlightText);
      if (cancelled) return;
      setMatch(found);
      if (!found) onHighlightMiss?.();
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightText, numPages, useGeometric]);

  // F2.1 A2' — GEOMETRIC highlight: when the chunk carries an exact Marker
  // region, scale its bbox from native PDF points to the current render width
  // and draw the box directly. No text search; exact to what the agent used.
  useEffect(() => {
    const pdf = pdfRef.current;
    // Only this effect owns `highlight` in geometric mode; do nothing otherwise.
    if (!useGeometric) return;
    let cancelled = false;
    void (async () => {
      if (!pdf || width <= 0 || bbox == null || bboxPage == null) {
        if (!cancelled) setHighlight(null);
        return;
      }
      const pageNumber = markerPageToPageNumber(bboxPage);
      if (pageNumber < 1 || pageNumber > pdf.numPages) {
        if (!cancelled) {
          setHighlight(null);
          onHighlightMiss?.();
        }
        return;
      }
      const page = await pdf.getPage(pageNumber);
      const originalWidth = page.getViewport({ scale: 1 }).width;
      const rect = bboxToRect(bbox, originalWidth, width);
      if (cancelled) return;
      setHighlight({
        pageNumber,
        rects: [rect],
        key: `bbox:${pageNumber}:${bbox.join(",")}`,
      });
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [useGeometric, bboxPage, bbox?.join(","), width, numPages]);

  // Compute highlight rectangles from PDF geometry at the current render scale,
  // so the overlay aligns exactly with the canvas regardless of the text layer.
  useEffect(() => {
    const pdf = pdfRef.current;
    // The geometric effect owns `highlight` when a bbox is present — don't clobber.
    if (useGeometric) return;
    if (!pdf || !match || width <= 0) {
      setHighlight(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      const page = await pdf.getPage(match.pageNumber);
      const scale = width / page.getViewport({ scale: 1 }).width;
      const viewport = page.getViewport({ scale });
      const text = await page.getTextContent();
      const rects: Rect[] = [];
      for (const idx of match.itemIndexes) {
        const item = text.items[idx];
        if (!item || !("transform" in item)) continue;
        const tx = pdfjs.Util.transform(
          viewport.transform,
          item.transform,
        ) as number[];
        const fontHeight = Math.hypot(tx[2] ?? 0, tx[3] ?? 0);
        rects.push({
          left: tx[4] ?? 0,
          top: (tx[5] ?? 0) - fontHeight,
          width: item.width * scale,
          height: fontHeight,
        });
      }
      if (cancelled) return;
      setHighlight({
        pageNumber: match.pageNumber,
        rects,
        key: `${match.pageNumber}:${[...match.itemIndexes].join(",")}`,
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [match, width, useGeometric]);

  // Scroll the highlight into view when its overlay mounts (remounts per the
  // `key`, i.e. once per cited passage — not on resize-driven rect updates).
  const scrollHighlightIntoView = useCallback((el: HTMLDivElement | null) => {
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, []);

  return (
    <div
      ref={containerRef}
      className="h-full w-full overflow-auto bg-neutral-100 p-3 dark:bg-neutral-900"
    >
      <Document
        file={file}
        onLoadSuccess={(pdf) => {
          pdfRef.current = pdf;
          pageTextRef.current = null;
          setNumPages(pdf.numPages);
        }}
        loading={<PaperLoading label="Loading PDF…" />}
        error={
          <div className="p-4 text-xs text-destructive">
            Couldn&apos;t render this PDF.
          </div>
        }
      >
        {Array.from({ length: numPages }, (_, i) => {
          const isTarget = highlight?.pageNumber === i + 1;
          return (
            <Page
              key={i}
              pageNumber={i + 1}
              width={width > 0 ? width : undefined}
              className="relative mx-auto mb-3 shadow"
              renderAnnotationLayer={false}
            >
              {isTarget && highlight && (
                <div
                  key={`${highlight.key}:${nonce}`}
                  ref={scrollHighlightIntoView}
                  className="pointer-events-none absolute inset-0 z-10"
                >
                  {highlight.rects.map((r, k) => (
                    <div
                      key={k}
                      className="absolute rounded-[2px] bg-yellow-300/45"
                      style={{
                        left: r.left,
                        top: r.top,
                        width: r.width,
                        height: r.height,
                      }}
                    />
                  ))}
                </div>
              )}
            </Page>
          );
        })}
      </Document>
    </div>
  );
}
