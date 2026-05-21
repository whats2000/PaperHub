import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { PaperLoading } from "@/components/canvas/PaperLoading";
import { HIGHLIGHT_CLASS } from "@/lib/findAndHighlight";
import { locatePassage, type PdfPassageMatch } from "@/lib/pdfHighlight";

// pdf.js needs a worker; resolve it from the installed pdfjs-dist via Vite's
// import.meta.url so the worker is bundled + served from the app origin.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const escapeHtml = (s: string): string =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

interface Props {
  /** Raw PDF bytes (fetched same-origin via the API). */
  data: Uint8Array;
  /** Cited passage to highlight + scroll to, when this PDF is the cited paper. */
  highlightText?: string | null;
  /** Called when the passage couldn't be located in the PDF text. */
  onHighlightMiss?: () => void;
}

/**
 * Renders a PDF inline as scrollable canvas pages via react-pdf — no iframe, so
 * no cross-origin issue and no browser download. The text layer is rendered so
 * the cited passage can be located and highlighted (the matched pdf.js text
 * items are wrapped in `<mark>` via `customTextRenderer`) and scrolled to.
 */
export function PdfView({ data, highlightText, onHighlightMiss }: Props) {
  const [numPages, setNumPages] = useState(0);
  const [width, setWidth] = useState(0);
  const [match, setMatch] = useState<PdfPassageMatch | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const pdfRef = useRef<PDFDocumentProxy | null>(null);
  // Per-document text-item index, built once and reused across passage changes.
  const pageTextRef = useRef<string[][] | null>(null);

  // pdfjs TRANSFERS (detaches) the ArrayBuffer it's given to its worker, so
  // passing the cached bytes directly would corrupt them and make a second
  // render ("switch away and back") fail with "Couldn't render this PDF". Give
  // pdfjs a fresh COPY each mount and keep the cached original intact.
  // (`file` is memoized so react-pdf — which compares by reference — doesn't
  // reload on unrelated re-renders.)
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

  // Locate the cited passage once the document is loaded (numPages flips after
  // onLoadSuccess, by which point pdfRef is set) or when the passage changes.
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

  // Wrap the matched text items in <mark> on the target page (other pages get
  // the default renderer). Keyed on `match` so it re-applies on a new citation.
  const renderHighlightedText = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }) =>
      match && match.itemIndexes.has(itemIndex)
        ? `<mark class="${HIGHLIGHT_CLASS}">${escapeHtml(str)}</mark>`
        : escapeHtml(str),
    [match],
  );

  // Scroll to the highlight once its text layer has rendered into the DOM.
  const scrollToHighlight = useCallback(() => {
    const el = containerRef.current?.querySelector(`.${HIGHLIGHT_CLASS}`);
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
          const isTarget = match?.pageNumber === i + 1;
          return (
            <Page
              key={i}
              pageNumber={i + 1}
              width={width > 0 ? width : undefined}
              className="mx-auto mb-3 shadow"
              renderAnnotationLayer={false}
              customTextRenderer={isTarget ? renderHighlightedText : undefined}
              onRenderTextLayerSuccess={isTarget ? scrollToHighlight : undefined}
            />
          );
        })}
      </Document>
    </div>
  );
}
