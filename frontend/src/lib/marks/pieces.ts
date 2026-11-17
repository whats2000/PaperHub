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

/** A piece located in the paper's reading text (a PDF page without Marker
 * blocks): its paragraph there (none when it crosses a break, or when it
 * wasn't located: never joins). */
export function locatedPlace(paragraph: number | null | undefined): string | null {
  return typeof paragraph === "number" ? `l:${paragraph}` : null;
}

/** Whether a piece is still on the paper (neither erased nor split away). */
export const isLive = (p: MarkPiece) => !p.erased && !p.removed;

/** The indexes of the pieces still on the paper. */
export function livePieceIndexes(pieces: readonly MarkPiece[]): number[] {
  return pieces.flatMap((p, i) => (isLive(p) ? [i] : []));
}

const num = (v: unknown, fallback = Infinity) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

type Quad = [number, number, number, number];

function readingKey(p: MarkPiece, pdf: boolean): number[] {
  if (pdf) {
    const boxes = Array.isArray(p.rects) ? (p.rects as Quad[]) : p.box ? [p.box] : [];
    const top = boxes.length ? Math.min(...boxes.map((b) => num(b[1]))) : Infinity;
    const left = boxes.length ? Math.min(...boxes.map((b) => num(b[0]))) : Infinity;
    return [num(p.page), top, left];
  }
  return [num(p.chunk_start), num(p.text_start, 0)];
}

/** Pieces as the paper reads them, for showing only: the stored list stays
 * in stroke order (erase, restore and split address pieces by index; the
 * last live one is the newest). A PDF piece (it has a page) reads by page,
 * then top, then left; an HTML piece by its chunk, then its offset in the
 * reading text. Mirrors the backend's `reading_order` (notes/marks.py). */
export function readingOrder<P extends MarkPiece>(pieces: readonly P[]): P[] {
  const pdf = pieces.length > 0 && pieces.every((p) => typeof p.page === "number");
  const keyed = pieces.map((p, i) => ({ p, i, k: readingKey(p, pdf) }));
  keyed.sort((a, b) => {
    for (let j = 0; j < a.k.length; j++) if (a.k[j] !== b.k[j]) return a.k[j]! < b.k[j]! ? -1 : 1;
    return a.i - b.i;
  });
  return keyed.map((x) => x.p);
}

/** One quote for a mark of several pieces, in reading order. Pieces of one
 * stroke (a selection across paragraphs; the server's `stroke` id) are
 * joined by a paragraph break; pieces of strokes joined later by "…", which
 * stands for skipped words only. A piece with no `stroke` is a stroke of
 * its own. Mirrors the backend's `joined_quote`. */
export function joinedQuote(pieces: readonly MarkPiece[]): string {
  let out = "";
  let prev: unknown = undefined;
  readingOrder(pieces.filter(isLive)).forEach((p, i) => {
    const q = String(p.quote ?? "").trim();
    if (!q) return;
    const stroke = typeof p.stroke === "number" ? p.stroke : `own:${i}`;
    if (out) out += stroke === prev ? "\n" : " … ";
    out += q;
    prev = stroke;
  });
  return out;
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
