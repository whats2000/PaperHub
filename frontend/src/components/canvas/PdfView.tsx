import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// pdf.js needs a worker; resolve it from the installed pdfjs-dist via Vite's
// import.meta.url so the worker is bundled + served from the app origin.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface Props {
  /** Raw PDF bytes (fetched same-origin via the API). */
  data: Uint8Array;
}

/**
 * Renders a PDF inline as scrollable canvas pages via react-pdf — no iframe, so
 * no cross-origin issue and no browser download. The PDF is shown as-is (no
 * passage highlighting; PDF papers carry the inline "highlighting unavailable"
 * note in the canvas).
 */
export function PdfView({ data }: Props) {
  const [numPages, setNumPages] = useState(0);
  const [width, setWidth] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

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

  return (
    <div
      ref={containerRef}
      className="h-full w-full overflow-auto bg-neutral-100 p-3 dark:bg-neutral-900"
    >
      <Document
        file={file}
        onLoadSuccess={({ numPages: n }) => setNumPages(n)}
        loading={
          <div className="p-4 text-xs text-muted-foreground">Loading PDF…</div>
        }
        error={
          <div className="p-4 text-xs text-destructive">
            Couldn&apos;t render this PDF.
          </div>
        }
      >
        {Array.from({ length: numPages }, (_, i) => (
          <Page
            key={i}
            pageNumber={i + 1}
            width={width > 0 ? width : undefined}
            className="mx-auto mb-3 shadow"
            renderAnnotationLayer={false}
            renderTextLayer={false}
          />
        ))}
      </Document>
    </div>
  );
}
