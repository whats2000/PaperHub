import { describe, expect, it } from "vitest";

import {
  RAIL_CLEAR_PX,
  TOC_ROW_PX,
  TOC_LIST_PAD_PX,
  listLayout,
  railGap,
  railRight,
  scrollRowIntoList,
} from "./tocRailLayout";

describe("the rail's place", () => {
  it("sits inside the scroll area, clear of the note's scrollbar", () => {
    expect(railRight({ offsetWidth: 600, clientWidth: 585 })).toBe(15 + RAIL_CLEAR_PX);
    expect(railRight({ offsetWidth: 600, clientWidth: 600 })).toBe(RAIL_CLEAR_PX);
    expect(railRight(null)).toBe(RAIL_CLEAR_PX);
    expect(RAIL_CLEAR_PX).toBeGreaterThanOrEqual(8);
  });

  it("closes its line gap so the whole rail fits half the pane", () => {
    expect(railGap(5, 800)).toBe(8); // few entries: the widest gap
    const n = 43;
    const gap = railGap(n, 600);
    expect(gap).toBeLessThan(8);
    expect(gap).toBeGreaterThanOrEqual(2);
    expect(n * 2 + (n - 1) * gap).toBeLessThanOrEqual(600 * 0.5);
    expect(railGap(200, 300)).toBe(2); // never below 2 px
    expect(railGap(1, 300)).toBe(8);
  });
});

describe("the list's layout", () => {
  const lineY = (i: number) => 120 + i * 6; // a rail line every 6 px from y=120

  it("a short list puts the current row at its line's height", () => {
    const n = 5;
    const cur = 3;
    const l = listLayout({ count: n, current: cur, lineY, paneTop: -24, paneHeight: 800, paneWidth: 600 });
    expect(l.height).toBe(n * TOC_ROW_PX + TOC_LIST_PAD_PX * 2);
    expect(l.scrollTop).toBe(0);
    // The row's centre, in the wrapper's coordinates.
    expect(l.top + TOC_LIST_PAD_PX + cur * TOC_ROW_PX + TOC_ROW_PX / 2 - l.scrollTop).toBe(lineY(cur));
    expect(l.width).toBe(264);
  });

  it("a long list is capped at 60% of the pane and opens scrolled so the current row sits at its line", () => {
    const n = 43;
    const cur = 30;
    const paneHeight = 600;
    const l = listLayout({ count: n, current: cur, lineY, paneTop: -24, paneHeight, paneWidth: 250 });
    expect(l.height).toBe(Math.round(paneHeight * 0.6));
    expect(l.scrollTop).toBeGreaterThan(0);
    expect(l.top + TOC_LIST_PAD_PX + cur * TOC_ROW_PX + TOC_ROW_PX / 2 - l.scrollTop).toBe(lineY(cur));
    expect(l.width).toBe(250 - 24);
  });

  it("stays inside the pane", () => {
    const l = listLayout({ count: 43, current: 0, lineY: () => -500, paneTop: -24, paneHeight: 600, paneWidth: 600 });
    expect(l.top).toBeGreaterThanOrEqual(-24 + 6);
    expect(l.top + l.height).toBeLessThanOrEqual(-24 + 600 - 6);
    expect(l.scrollTop).toBeGreaterThanOrEqual(0);
    expect(l.scrollTop).toBeLessThanOrEqual(43 * TOC_ROW_PX + TOC_LIST_PAD_PX * 2 - l.height);
  });
});

describe("moving through the list by key", () => {
  it("scrolls a row below the view up into it, and one above down into it", () => {
    // 200 px list, rows 26 px from 6 px.
    expect(scrollRowIntoList({ rowTop: 300, rowHeight: 26, scrollTop: 0, clientHeight: 200 })).toBe(300 + 26 - 200 + TOC_LIST_PAD_PX);
    expect(scrollRowIntoList({ rowTop: 50, rowHeight: 26, scrollTop: 100, clientHeight: 200 })).toBe(50 - TOC_LIST_PAD_PX);
    expect(scrollRowIntoList({ rowTop: 120, rowHeight: 26, scrollTop: 100, clientHeight: 200 })).toBe(100);
  });
});
