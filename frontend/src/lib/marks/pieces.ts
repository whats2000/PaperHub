/** The pieces of a mark and the highlighter's joining rule (Plan H5b.1).
 *
 * A highlighter stroke of the same colour in the same paragraph (HTML) or
 * Marker block (PDF) as a mark made while the pen was on in this pen session
 * joins that mark as one more piece; the paper shows the pieces with the
 * skipped words unmarked. A place is a string: `h:<n>` for an HTML
 * paragraph element (named for this page's life), `p:<page>:<block>` for a
 * PDF Marker block. */
import type { MarkColor, MarkPiece } from "@/types/notes";

/** A mark the pen made in this pen session, and the places it covers. */
export interface PenMark {
  id: string;
  color: MarkColor;
  places: readonly string[];
}

export interface Stroke {
  color: MarkColor;
  /** Null when the stroke's paragraph / block is unknown (never joins). */
  place: string | null;
}

/** The mark a stroke joins (the newest match), or null for a new mark. */
export function joinTarget(marks: readonly PenMark[], stroke: Stroke): string | null {
  if (stroke.place === null) return null;
  for (let i = marks.length - 1; i >= 0; i--) {
    const m = marks[i]!;
    if (m.color === stroke.color && m.places.includes(stroke.place)) return m.id;
  }
  return null;
}

const elementPlaces = new WeakMap<Element, string>();
let nextPlace = 0;

/** An HTML paragraph's place: the same element, the same name. */
export function htmlPlace(el: Element): string {
  let p = elementPlaces.get(el);
  if (!p) {
    nextPlace += 1;
    p = `h:${nextPlace}`;
    elementPlaces.set(el, p);
  }
  return p;
}

/** A PDF piece's place: its page and Marker block (none without blocks). */
export function pdfPlace(page: number, blockId: string | null | undefined): string | null {
  return blockId ? `p:${page}:${blockId}` : null;
}

/** Whether a piece is still on the paper (neither erased nor split away). */
export const isLive = (p: MarkPiece) => !p.erased && !p.removed;

/** The indexes of the pieces still on the paper. */
export function livePieceIndexes(pieces: readonly MarkPiece[]): number[] {
  return pieces.flatMap((p, i) => (isLive(p) ? [i] : []));
}

/** One quote for a mark of several pieces: "…" between them. */
export function joinedQuote(pieces: readonly MarkPiece[]): string {
  return pieces
    .filter(isLive)
    .map((p) => String(p.quote ?? "").trim())
    .filter(Boolean)
    .join(" … ");
}

/** Clears the selection if it is still `range` (a stroke just saved; a new
 * drag since then is left alone). */
export function clearIfStill(doc: Document, range: Range): void {
  const sel = doc.getSelection();
  if (!sel?.rangeCount) return;
  const cur = sel.getRangeAt(0);
  if (
    cur.compareBoundaryPoints(range.START_TO_START, range) === 0 &&
    cur.compareBoundaryPoints(range.END_TO_END, range) === 0
  ) {
    sel.removeAllRanges();
  }
}
