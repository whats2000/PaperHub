/**
 * F2.1 A2' — exact geometric highlight from a Marker chunk's stored region.
 *
 * Marker-ingested chunks carry the EXACT region the slide/answer agent used:
 *   - `page`: the 0-based ABSOLUTE page index Marker stored it under.
 *   - `bbox`: `[x0, y0, x1, y1]` in PDF points, origin TOP-LEFT, in the page's
 *     native coordinate space (e.g. a 612×792pt Letter page).
 *
 * When present we draw the Citation Canvas PDF highlight straight from this
 * geometry — no text search, no fuzzy drift on tables/figures. Non-Marker
 * (PyMuPDF / LaTeX-source) chunks have no bbox and fall back to text search.
 *
 * pdf.js renders a page at `scale = renderedWidth / originalWidth`; because the
 * bbox already uses a top-left origin (the same convention as the rendered
 * viewport's CSS box), the conversion is a single uniform scale — no Y-axis
 * flip needed.
 */

export interface HighlightRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Scale a native-PDF-point bbox (`[x0,y0,x1,y1]`, top-left origin) into a CSS
 * rect positioned over a react-pdf `<Page>` rendered at `renderedWidth` px.
 */
export function bboxToRect(
  bbox: readonly number[],
  originalWidth: number,
  renderedWidth: number,
): HighlightRect {
  const scale = renderedWidth / originalWidth;
  const x0 = bbox[0] ?? 0;
  const y0 = bbox[1] ?? 0;
  const x1 = bbox[2] ?? 0;
  const y1 = bbox[3] ?? 0;
  return {
    left: x0 * scale,
    top: y0 * scale,
    width: (x1 - x0) * scale,
    height: (y1 - y0) * scale,
  };
}

/**
 * Marker stores pages as 0-based absolute indices; react-pdf's `<Page>` uses
 * 1-based `pageNumber`. (Verified against PdfView's own page rendering, which
 * maps array index `i` → `pageNumber={i + 1}`.)
 */
export function markerPageToPageNumber(markerPage: number): number {
  return markerPage + 1;
}

/**
 * True when a chunk carries a usable geometric region: a non-null `page` AND a
 * well-formed length-4 `bbox`. Anything else → caller uses the text-search
 * fallback (`resolveNeedle` → `locatePassage`).
 */
export function hasGeometricRegion(chunk: {
  page?: number | null;
  bbox?: readonly number[] | null;
}): boolean {
  return (
    chunk.page != null &&
    Array.isArray(chunk.bbox) &&
    chunk.bbox.length === 4
  );
}
