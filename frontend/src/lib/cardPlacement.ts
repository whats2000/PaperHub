import { markerPageToPageNumber } from "@/lib/bboxHighlight";

/** Distance kept between the card and the viewport's top/bottom edges. */
export const CARD_EDGE = 8;
/** Below this gutter width the card drops under the paragraph, inside the reader. */
export const NARROW_GUTTER_PX = 260;

const CARD_MAX_WIDTH = 460;
/** In the below fallback, the card takes at most this share of the viewport. */
const BELOW_MAX_SHARE = 0.45;
const BELOW_GAP = 10;
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
}

export interface CardPlacement {
  /** `left`: in the gutter, arrow right. `below`: inside the reader, arrow up. */
  side: "left" | "below";
  /** Fixed (client) coordinates of the card's top-left corner. */
  top: number;
  left: number;
  width: number;
  maxHeight: number;
  /** Arrow offset from the card's top (side card; 0 for `below`). */
  arrowY: number;
  /** Arrow offset from the card's left (below card; 0 for `left`). */
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
 * visible, `maxHeight` = the visible height), otherwise under the paragraph
 * inside the reader (`maxHeight` ≤ 45 % of the viewport). For a paragraph split
 * across columns or pages, the fragment most in view is the anchor. Returns
 * `null` when no fragment is in view.
 */
export function placeCard({
  paraRects,
  viewport,
  gutter,
  cardHeight,
  reader,
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
  if (active < 0) return null;

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
  const h = Math.min(cardHeight, maxHeight);
  const boundLeft = (reader?.left ?? r.left) + READER_MARGIN;
  const boundRight = (reader?.right ?? r.right) - READER_MARGIN;
  const width = Math.max(
    0,
    Math.min(Math.max(r.width, NARROW_GUTTER_PX), CARD_MAX_WIDTH, boundRight - boundLeft),
  );
  const left = clamp(r.left, boundLeft, boundRight - width);
  let top = visBottom + BELOW_GAP;
  if (top + h > viewport.bottom - CARD_EDGE) {
    top = Math.max(viewport.top + CARD_EDGE, viewport.bottom - CARD_EDGE - h);
  }
  const arrowX = clamp(r.left + 2 * ARROW - left, ARROW_INSET + 4, Math.max(14, width - 3 * ARROW));
  return {
    side: "below",
    top,
    left,
    width,
    maxHeight,
    arrowY: 0,
    arrowX,
    activeFragment: active,
    scrollFraction,
  };
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
