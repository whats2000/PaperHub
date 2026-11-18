/** The pieces of a mark and the highlighter's joining rule (Plan H5b.1).
 *
 * A highlighter stroke of the same colour in the same paragraph (HTML) or
 * Marker block (PDF) as a mark made while the pen was on in this pen session
 * joins that mark as one more piece; the paper shows the pieces with the
 * skipped words unmarked. A place is a string: `h:<n>` for an HTML
 * paragraph element (named for this page's life), `p:<page>:<block>` for a
 * PDF Marker block. */
import type { Mark, MarkColor, MarkPiece } from "@/types/notes";

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

/** A piece marked on a translation: the paragraph it translates (an HTML
 * paragraph's place, or a PDF paragraph's key), in that language. Never a
 * place of the paper's own text, so a stroke on a translation never joins
 * a mark on the original. */
export function translationPlace(lang: string, source: string | null): string | null {
  return source ? `t:${lang}:${source}` : null;
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
  // Pieces of one translated paragraph share their source's place: they
  // read by where they sit in the translation.
  const tr = num(p.tr_start, 0);
  if (pdf) {
    const boxes = Array.isArray(p.rects) ? (p.rects as Quad[]) : p.box ? [p.box] : [];
    const top = boxes.length ? Math.min(...boxes.map((b) => num(b[1]))) : Infinity;
    const left = boxes.length ? Math.min(...boxes.map((b) => num(b[0]))) : Infinity;
    return [num(p.page), top, left, tr];
  }
  return [num(p.chunk_start), num(p.text_start, 0), tr];
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
    // Only a whole number names a stroke (as the server reads it).
    const stroke = Number.isInteger(p.stroke) ? p.stroke : `own:${i}`;
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

// ---- Where a mark sits in the paper ------------------------------------

/** A chunk of the paper, in the paper's order (the reader's chunk anchors). */
export interface PaperChunk {
  id: number;
  section: string | null;
}

const isLivePiece = (p: MarkPiece) => !p.erased && !p.removed;

/** A piece's place on the pages: its page plus how far down (0–1) it starts. */
function pagePlace(p: MarkPiece): number | null {
  if (typeof p.page !== "number") return null;
  const boxes = Array.isArray(p.rects) ? (p.rects as Quad[]) : p.box ? [p.box] : [];
  const top = boxes.length ? Math.min(...boxes.map((b) => num(b[1], 0))) : 0;
  return p.page + Math.min(Math.max(top, 0), 0.999);
}

/**
 * One reading position per mark, comparable across every kind of anchor, so
 * a list of marks reads as the paper does:
 *
 * - a mark anchored to the text (a chunk) sits at its chunk's place in the
 *   paper, then its offset;
 * - a mark only on a page (a box, a figure, an equation, unlocated PDF text)
 *   is mapped onto the chunks through the marks that have both a chunk and
 *   a page (interpolated by page and height, kept within its section's
 *   chunks); with none, it sits at the start of its section's chunks, in
 *   page order;
 * - with no chunks at all (a PDF-only paper), every mark sits by page, then
 *   height.
 *
 * Each mark is placed by its first live piece in reading order.
 */
export function markPositions(marks: readonly Mark[], chunks: readonly PaperChunk[]): Map<string, number> {
  const ord = new Map(chunks.map((c, i) => [c.id, i] as const));
  const sectionStart = new Map<string, number>();
  const sectionEnd = new Map<string, number>();
  chunks.forEach((c, i) => {
    if (c.section !== null && !sectionStart.has(c.section)) sectionStart.set(c.section, i);
    if (c.section !== null) sectionEnd.set(c.section, i + 1);
  });
  const chunkPlace = (p: MarkPiece): number | null => {
    const at = typeof p.chunk_start === "number" ? ord.get(p.chunk_start) : undefined;
    if (at === undefined) return null;
    return at + Math.min(num(p.text_start, 0), 999_999) / 1e6;
  };
  const firsts = marks.map((m) => [m, readingOrder(m.pieces.filter(isLivePiece))[0]] as const);
  // Calibration: pieces placed both ways.
  const calib = marks
    .flatMap((m) => m.pieces.filter(isLivePiece))
    .map((p) => [pagePlace(p), chunkPlace(p)] as const)
    .filter((x): x is readonly [number, number] => x[0] !== null && x[1] !== null)
    .sort((a, b) => a[0] - b[0]);
  const onChunks = (x: number): number => {
    const first = calib[0]!;
    const last = calib[calib.length - 1]!;
    if (x <= first[0]) return first[1] - (first[0] - x) * 1e-3;
    if (x >= last[0]) return last[1] + (x - last[0]) * 1e-3;
    const i = calib.findIndex((c) => c[0] >= x);
    const [x0, y0] = calib[i - 1]!;
    const [x1, y1] = calib[i]!;
    return x1 === x0 ? y0 : y0 + ((x - x0) / (x1 - x0)) * (y1 - y0);
  };
  const out = new Map<string, number>();
  for (const [m, p] of firsts) {
    let pos = Infinity;
    if (p) {
      const c = chunkPlace(p);
      const pg = pagePlace(p);
      if (!chunks.length) pos = pg ?? num(p.chunk_start);
      else if (c !== null) pos = c;
      else if (pg !== null) {
        const start = m.section_key !== null ? sectionStart.get(m.section_key) : undefined;
        const end = m.section_key !== null ? sectionEnd.get(m.section_key) : undefined;
        pos = calib.length ? onChunks(pg) : start !== undefined ? start + pg / 1e4 : chunks.length + pg;
        // Never outside its own section: a calibrating mark far away (sparse
        // pairs) must not carry it past the sections between.
        if (start !== undefined && end !== undefined) pos = Math.min(Math.max(pos, start), end - 1e-6);
      }
    }
    out.set(m.id, pos);
  }
  return out;
}
