import { markerPageToPageNumber } from "@/lib/bboxHighlight";

/** Distance kept between the card and the viewport's top/bottom edges. */
export const CARD_EDGE = 8;
/**
 * The narrowest side card (px): below this gutter width the card drops under
 * the paragraph, inside the reader, rather than squeeze into the gutter. Also
 * the in-reader card's minimum width.
 */
export const NARROW_GUTTER_PX = 360;

const CARD_MAX_WIDTH = 460;
/** In the below fallback, the card takes at most this share of the viewport. */
const BELOW_MAX_SHARE = 0.45;
const BELOW_GAP = 10;
/** Least room (px) a card shortened to fit beside the paragraph may have; below it the card covers the paragraph. */
const MIN_CARD_ROOM = 96;
const READER_MARGIN = 12;
/** The arrow is a 10px rotated square. */
const ARROW = 10;
const ARROW_INSET = 10;

interface Rect {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
}

export interface PlaceCardInput {
  /** Client rects of the paragraph's fragments, in reading order. */
  paraRects: readonly Rect[];
  /** The reading area's visible vertical extent (client coordinates). */
  viewport: { top: number; bottom: number };
  /** The free space left of the reader that the side card may use. */
  gutter: { left: number; right: number };
  /** The card's current rendered height. */
  cardHeight: number;
  /** The reader's horizontal extent, bounding the below fallback. */
  reader?: { left: number; right: number };
  /** A pinned card never steps aside: with its paragraph out of view it docks
   *  to the viewport edge the paragraph left by. */
  pinned?: boolean;
}

export interface CardPlacement {
  /**
   * `left`: in the gutter, arrow right. `below`: inside the reader under the
   * paragraph, arrow up. `above`: over the paragraph, arrow down.
   */
  side: "left" | "below" | "above";
  /** Fixed (client) coordinates of the card's top-left corner. */
  top: number;
  left: number;
  width: number;
  maxHeight: number;
  /** Arrow offset from the card's top (side card; 0 for `below`). */
  arrowY: number;
  /** Arrow offset from the card's left (below / above card; 0 for `left`). */
  arrowX: number;
  /** Index into `paraRects` of the fragment most in view; the arrow tracks it. */
  activeFragment: number;
  /** Share (0–1) of the active fragment scrolled past the viewport top. */
  scrollFraction: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, hi));

/**
 * Where the PDF translation card goes. Beside the reader when the gutter is at
 * least 260px wide (clamped inside the viewport while the paragraph is
 * visible, `maxHeight` = the visible height), otherwise inside the reader under
 * the paragraph, or over it when there is no room below (`maxHeight` ≤ 45 % of
 * the viewport). When neither side holds the whole card it takes the roomier
 * side, shortened; it covers the paragraph only when neither side has a usable
 * strip left. For a paragraph split
 * across columns or pages, the fragment most in view is the anchor. Returns
 * `null` when no fragment is in view.
 */
export function placeCard({
  paraRects,
  viewport,
  gutter,
  cardHeight,
  reader,
  pinned = false,
}: PlaceCardInput): CardPlacement | null {
  let active = -1;
  let best = 0;
  paraRects.forEach((r, i) => {
    const visible = Math.min(r.bottom, viewport.bottom) - Math.max(r.top, viewport.top);
    if (visible > best) {
      best = visible;
      active = i;
    }
  });
  if (active < 0) {
    return pinned ? dockPinned({ paraRects, viewport, gutter, cardHeight, reader }) : null;
  }

  const r = paraRects[active]!;
  const visTop = Math.max(r.top, viewport.top);
  const visBottom = Math.min(r.bottom, viewport.bottom);
  const viewHeight = viewport.bottom - viewport.top;
  const scrollFraction = r.height > 0 ? clamp((viewport.top - r.top) / r.height, 0, 1) : 0;

  const gutterWidth = gutter.right - gutter.left;
  if (gutterWidth >= NARROW_GUTTER_PX) {
    const maxHeight = Math.max(0, viewHeight - 2 * CARD_EDGE);
    const h = Math.min(cardHeight, maxHeight);
    const top = clamp(r.top, viewport.top + CARD_EDGE, viewport.bottom - CARD_EDGE - h);
    const width = Math.min(CARD_MAX_WIDTH, gutterWidth);
    const arrowY = clamp(
      (visTop + visBottom) / 2 - top - ARROW / 2,
      ARROW_INSET,
      Math.max(ARROW_INSET, h - ARROW_INSET - ARROW),
    );
    return {
      side: "left",
      top,
      left: gutter.right - width,
      width,
      maxHeight,
      arrowY,
      arrowX: 0,
      activeFragment: active,
      scrollFraction,
    };
  }

  const maxHeight = Math.floor(viewHeight * BELOW_MAX_SHARE);
  // A card not yet measured (0) picks its side by its tallest size, so it
  // doesn't flip across the paragraph once it is measured.
  const h = cardHeight > 0 ? Math.min(cardHeight, maxHeight) : maxHeight;
  const boundLeft = (reader?.left ?? r.left) + READER_MARGIN;
  const boundRight = (reader?.right ?? r.right) - READER_MARGIN;
  const width = Math.max(
    0,
    Math.min(Math.max(r.width, NARROW_GUTTER_PX), CARD_MAX_WIDTH, boundRight - boundLeft),
  );
  const left = clamp(r.left, boundLeft, boundRight - width);
  // Room for the card under and over the visible part of the paragraph.
  const roomBelow = viewport.bottom - CARD_EDGE - (visBottom + BELOW_GAP);
  const roomAbove = visTop - BELOW_GAP - (viewport.top + CARD_EDGE);
  let side: "below" | "above" = "below";
  let top: number;
  let cap = maxHeight;
  if (h <= roomBelow) {
    top = visBottom + BELOW_GAP;
  } else if (h <= roomAbove) {
    side = "above";
    top = visTop - BELOW_GAP - h;
  } else if (Math.max(roomBelow, roomAbove) >= MIN_CARD_ROOM) {
    // Neither side holds it all: the roomier side, shortened (its content scrolls).
    if (roomBelow >= roomAbove) {
      cap = Math.floor(roomBelow);
      top = visBottom + BELOW_GAP;
    } else {
      side = "above";
      cap = Math.floor(roomAbove);
      top = visTop - BELOW_GAP - Math.min(h, cap);
    }
  } else {
    // The paragraph fills the view: lift the card off the bottom edge, over it.
    top = Math.max(viewport.top + CARD_EDGE, viewport.bottom - CARD_EDGE - h);
  }
  const arrowX = clamp(r.left + 2 * ARROW - left, ARROW_INSET + 4, Math.max(14, width - 3 * ARROW));
  return {
    side,
    top,
    left,
    width,
    maxHeight: cap,
    arrowY: 0,
    arrowX,
    activeFragment: active,
    scrollFraction,
  };
}

/**
 * A pinned card whose paragraph is out of view: it stays on screen, docked to
 * the viewport edge nearest the paragraph (top when the paragraph scrolled out
 * above, bottom when it is below), its arrow toward it.
 */
function dockPinned({
  paraRects,
  viewport,
  gutter,
  cardHeight,
  reader,
}: Omit<PlaceCardInput, "pinned">): CardPlacement | null {
  if (!paraRects.length) return null;
  // The fragment nearest the viewport.
  const dist = (r: Rect) =>
    r.bottom <= viewport.top ? viewport.top - r.bottom : r.top - viewport.bottom;
  let active = 0;
  paraRects.forEach((r, i) => {
    if (dist(r) < dist(paraRects[active]!)) active = i;
  });
  const r = paraRects[active]!;
  const above = r.bottom <= viewport.top;
  const viewHeight = viewport.bottom - viewport.top;
  const scrollFraction = above ? 1 : 0;
  const gutterWidth = gutter.right - gutter.left;
  if (gutterWidth >= NARROW_GUTTER_PX) {
    const maxHeight = Math.max(0, viewHeight - 2 * CARD_EDGE);
    const h = Math.min(cardHeight, maxHeight);
    const top = above ? viewport.top + CARD_EDGE : viewport.bottom - CARD_EDGE - h;
    const width = Math.min(CARD_MAX_WIDTH, gutterWidth);
    return {
      side: "left",
      top,
      left: gutter.right - width,
      width,
      maxHeight,
      arrowY: above ? ARROW_INSET : Math.max(ARROW_INSET, h - ARROW_INSET - ARROW),
      arrowX: 0,
      activeFragment: active,
      scrollFraction,
    };
  }
  const maxHeight = Math.floor(viewHeight * BELOW_MAX_SHARE);
  const h = cardHeight > 0 ? Math.min(cardHeight, maxHeight) : maxHeight;
  const boundLeft = (reader?.left ?? r.left) + READER_MARGIN;
  const boundRight = (reader?.right ?? r.right) - READER_MARGIN;
  const width = Math.max(
    0,
    Math.min(Math.max(r.width, NARROW_GUTTER_PX), CARD_MAX_WIDTH, boundRight - boundLeft),
  );
  const left = clamp(r.left, boundLeft, boundRight - width);
  return {
    // "below" a paragraph above the view: arrow up; "above" one below it.
    side: above ? "below" : "above",
    top: above ? viewport.top + CARD_EDGE : viewport.bottom - CARD_EDGE - h,
    left,
    width,
    maxHeight,
    arrowY: 0,
    arrowX: clamp(r.left + 2 * ARROW - left, ARROW_INSET + 4, Math.max(14, width - 3 * ARROW)),
    activeFragment: active,
    scrollFraction,
  };
}

/**
 * Whether a move of the card from `from` to `to` may glide. Never between
 * under and over the paragraph: the glide would sweep the card across the
 * text it translates (and under the pointer reading it).
 */
export function glidesBetween(from: CardPlacement["side"] | null, to: CardPlacement["side"]): boolean {
  const inReader = (s: CardPlacement["side"] | null) => s === "below" || s === "above";
  return !(inReader(from) && inReader(to) && from !== to);
}

/**
 * The card content's scrollTop for the reader's progress: `fraction` of the
 * active fragment's span, where the spans are cut at the `⟦BR⟧` dividers
 * (`breaks`: each divider's offset within the scroll content).
 */
export function contentScrollTop({
  fraction,
  activeFragment,
  breaks,
  range,
}: {
  fraction: number;
  activeFragment: number;
  breaks: readonly number[];
  range: number;
}): number {
  if (range <= 0) return 0;
  const cut = (i: number) => clamp(breaks[i] ?? range, 0, range);
  const lo = activeFragment === 0 ? 0 : cut(activeFragment - 1);
  const hi = activeFragment < breaks.length ? cut(activeFragment) : range;
  return lo + Math.max(0, hi - lo) * clamp(fraction, 0, 1);
}

interface Fragment {
  /** 0-based Marker page. */
  page: number;
  bbox: readonly number[];
}

export type BreakKind = { kind: "page"; page: number } | { kind: "column" } | { kind: "none" };

/**
 * What a `⟦BR⟧` between two fragments means for the reader: the paragraph goes
 * on on another page (1-based), or in the next column of the same page (the
 * next fragment starts at or past the current one's right edge or horizontal
 * midpoint). Anything else (a bridge over a display equation or a page
 * header/footer in the same column) is `none`: an unlabelled divider.
 */
export function describeBreak(current: Fragment, next: Fragment): BreakKind {
  if (next.page !== current.page) {
    return { kind: "page", page: markerPageToPageNumber(next.page) };
  }
  const [x0 = 0, , x1 = x0] = current.bbox;
  const nextX0 = next.bbox[0] ?? 0;
  if (nextX0 >= x1 || nextX0 > (x0 + x1) / 2) return { kind: "column" };
  return { kind: "none" };
}
