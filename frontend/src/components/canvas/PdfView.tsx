import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { PaperLoading } from "@/components/canvas/PaperLoading";
import { locatePassage, type PdfPassageMatch } from "@/lib/pdfHighlight";

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
export function PdfView({ data, highlightText, nonce = 0, onHighlightMiss }: Props) {
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
    if (!pdf || !highlightText) {
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
  }, [highlightText, numPages]);

  // Compute highlight rectangles from PDF geometry at the current render scale,
  // so the overlay aligns exactly with the canvas regardless of the text layer.
  useEffect(() => {
    const pdf = pdfRef.current;
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
  }, [match, width]);

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
