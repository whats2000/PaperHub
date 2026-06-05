import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

import { fetchDeckPdfData } from "@/lib/api";
import { createPresentChannel, type PresentChannel } from "@/lib/presentChannel";

// pdf.js worker — resolved from the installed pdfjs-dist via import.meta.url so
// the worker is bundled + served from the app origin (same as SlidesPanel).
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface Props {
  sessionId: number;
}

/**
 * PresentPage — the audience window. Slide-only, fullscreen, zero chrome.
 * Owns its OWN PDF bytes + page state, so closing the in-app Slides panel
 * (the Q&A loop) never affects it. Follows the presenter's page over
 * BroadcastChannel and answers heartbeat pings so the cockpit badge can show
 * "audience connected".
 */
export function PresentPage({ sessionId }: Props) {
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [width, setWidth] = useState(window.innerWidth);
  const chRef = useRef<PresentChannel | null>(null);

  // Fetch the compiled deck PDF once.
  useEffect(() => {
    let cancelled = false;
    fetchDeckPdfData(sessionId)
      .then((b) => {
        if (!cancelled) setBytes(b);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Channel: follow the presenter's page; answer pings; announce presence.
  useEffect(() => {
    const ch = createPresentChannel(sessionId);
    ch.onPage((p) => setPage(p));
    ch.onPing(() => ch.pong());
    ch.pong();
    chRef.current = ch;
    return () => ch.close();
  }, [sessionId]);

  // Refit on window resize. Fit by width only (a landscape Beamer slide fits
  // the viewport height at full width on a typical 16:9 / 4:3 projector).
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // pdfjs transfers (detaches) the ArrayBuffer to its worker, so pass a fresh
  // copy each render (same pattern as SlidesPanel/PdfView).
  const file = useMemo(() => (bytes ? { data: bytes.slice() } : null), [bytes]);
  const safePage = Math.min(Math.max(1, page), numPages || 1);

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        background: "#000",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
      }}
    >
      {file && (
        <Document
          file={file}
          onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
          loading=""
        >
          {/* Mount EVERY page once and toggle visibility (hidden = display:none
              on the inactive ones), so advancing a slide is a CSS show/hide of
              an already-rasterized canvas — no teardown/re-rasterize flash
              between pages. Same approach as SlidesPanel (v2.23.2). pdf.js
              rasterizes each page once on mount; memory is bounded by the deck
              budget (8–30 slides).

              Canvas-only: the audience is a pure display, so render just the
              slide image. The text + annotation layers are HTML overlays that
              need react-pdf's layer CSS — which only loads in the MAIN bundle
              (via PdfView), not in this separate present.html entry — so leaving
              them on rendered unstyled, overlapping HTML text. We don't want
              selection/links here anyway. */}
          {Array.from({ length: numPages }, (_, i) => {
            const pageNum = i + 1;
            const isActive = pageNum === safePage;
            return (
              <div
                key={pageNum}
                hidden={!isActive}
                aria-hidden={!isActive}
              >
                <Page
                  pageNumber={pageNum}
                  width={width}
                  loading=""
                  renderTextLayer={false}
                  renderAnnotationLayer={false}
                />
              </div>
            );
          })}
        </Document>
      )}
    </div>
  );
}
