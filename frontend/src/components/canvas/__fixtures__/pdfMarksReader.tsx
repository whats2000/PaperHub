/** A fake PDF reader for the PDF marks tests: one 600 x 800 page with a
 * three-span text layer (none on a scan), the marks layer on it, and the
 * popover. Helpers live in `./pdfMarks`. */
import { useRef } from "react";

import { PdfMarksPage } from "@/components/canvas/PdfMarksLayer";
import { PdfMarksProvider, usePdfMarks } from "@/components/canvas/usePdfMarks";

export function Reader({
  hasText = true,
  onReading,
  thirdLine,
}: {
  hasText?: boolean;
  /** A third line of text on the page (the text below the top lines). */
  thirdLine?: string;
  onReading?: (heading: string | null) => void;
}) {
  const readerRef = useRef<HTMLDivElement>(null);
  const { ctx, popover } = usePdfMarks({
    paperId: 3,
    readerRef,
    onReadingHeading: onReading ? (h) => onReading(h) : undefined,
  });
  return (
    <div>
      {/* Focusable, as PdfView's scroll container: Escape gives focus back here. */}
      <div ref={readerRef} data-testid="reader" tabIndex={-1}>
        <PdfMarksProvider value={ctx}>
          <div className="react-pdf__Page" data-page-number="1" data-testid="page-1">
            <div className="react-pdf__Page__textContent textLayer">
              {hasText && (
                <>
                  <span data-testid="s1">Robots learn </span>
                  <span data-testid="s2">from demos.</span>
                  <br />
                  <span data-testid="s3">Next line here.</span>
                  {thirdLine && (
                    <>
                      <br />
                      <span data-testid="s4">{thirdLine}</span>
                    </>
                  )}
                </>
              )}
            </div>
            <PdfMarksPage pageNumber={1} hasTextLayer={hasText} />
          </div>
        </PdfMarksProvider>
      </div>
      {popover}
    </div>
  );
}
