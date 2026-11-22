/** Geometry of the floating table of contents (TocRail.tsx): where the rail
 * sits, how close its lines are, and where the list opens so that each line
 * grows into its own row. Pure: the component measures, these decide. */

/** Space kept between the rail and the note's scrollbar. */
export const RAIL_CLEAR_PX = 8;
/** One row of the open list. */
export const TOC_ROW_PX = 26;
/** The list's padding above the first and below the last row. */
export const TOC_LIST_PAD_PX = 6;
/** The rail's lines: 2 px tall. */
const LINE_PX = 2;
const MAX_LIST_WIDTH = 264;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** The rail's right offset in the pane: inside the scroll area, clear of the
 * scroller's scrollbar (its width is what the scroller's border box has
 * beyond its client box). */
export function railRight(scroller: { offsetWidth: number; clientWidth: number } | null): number {
  const bar = scroller ? Math.max(0, scroller.offsetWidth - scroller.clientWidth) : 0;
  return bar + RAIL_CLEAR_PX;
}

/** The gap between the rail's lines: 8 px, closing (to 2 px at least) so
 * that the whole rail fits half the pane. */
export function railGap(count: number, paneHeight: number): number {
  if (count < 2) return 8;
  const room = paneHeight * 0.5 - 12;
  return clamp((room - LINE_PX * count) / (count - 1), 2, 8);
}

export interface ListLayout {
  top: number;
  height: number;
  width: number;
  scrollTop: number;
}

/** Where the list opens, in the rail wrapper's coordinates: the whole list,
 * or 60% of the pane and scrolling; the current row lands at its line's
 * height (`lineY`: a line's centre), kept inside the pane (`paneTop`: the
 * pane's top in the same coordinates). */
export function listLayout(o: {
  count: number;
  current: number;
  lineY: (i: number) => number;
  paneTop: number;
  paneHeight: number;
  paneWidth: number;
}): ListLayout {
  const { count, current, lineY, paneTop, paneHeight, paneWidth } = o;
  const width = Math.min(MAX_LIST_WIDTH, paneWidth - 24);
  const full = count * TOC_ROW_PX + TOC_LIST_PAD_PX * 2;
  const height = Math.min(full, Math.round(paneHeight * 0.6));
  const lo = paneTop + 6;
  const hi = lo + paneHeight - 12;
  const ly = lineY(current);
  const rowCentre = TOC_LIST_PAD_PX + current * TOC_ROW_PX + TOC_ROW_PX / 2;
  let top = full <= height ? ly - rowCentre : (lineY(0) + lineY(count - 1)) / 2 - height / 2;
  top = clamp(top, lo, hi - height);
  const scrollTop = clamp(rowCentre - (ly - top), 0, full - height);
  return { top, height, width, scrollTop };
}

/** The list's scrollTop that brings a row moved to by key into view. */
export function scrollRowIntoList(o: {
  rowTop: number;
  rowHeight: number;
  scrollTop: number;
  clientHeight: number;
}): number {
  const { rowTop, rowHeight, scrollTop, clientHeight } = o;
  if (rowTop < scrollTop + TOC_LIST_PAD_PX) return rowTop - TOC_LIST_PAD_PX;
  if (rowTop + rowHeight > scrollTop + clientHeight - TOC_LIST_PAD_PX)
    return rowTop + rowHeight - clientHeight + TOC_LIST_PAD_PX;
  return scrollTop;
}
