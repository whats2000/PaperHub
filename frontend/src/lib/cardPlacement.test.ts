import { describe, expect, it } from "vitest";

import {
  CARD_EDGE,
  NARROW_GUTTER_PX,
  contentScrollTop,
  describeBreak,
  placeCard,
} from "@/lib/cardPlacement";

/** A client rect from top-left + size. */
const rect = (left: number, top: number, width: number, height: number) =>
  new DOMRect(left, top, width, height);

const VIEWPORT = { top: 100, bottom: 900 }; // 800px tall reading area
const WIDE = { left: 12, right: 588 }; // 576px gutter left of the reader
const READER = { left: 600, right: 1300 };

describe("placeCard — side placement", () => {
  it("sits in the gutter, as wide as it allows (max 460), aligned with the paragraph", () => {
    const p = placeCard({
      paraRects: [rect(640, 300, 500, 80)],
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 120,
      reader: READER,
    })!;
    expect(p.side).toBe("left");
    expect(p.top).toBe(300);
    expect(p.width).toBe(460);
    expect(p.left).toBe(588 - 460);
    expect(p.activeFragment).toBe(0);
    // A narrower gutter shrinks the card to the gutter.
    const narrow = placeCard({
      paraRects: [rect(640, 300, 500, 80)],
      viewport: VIEWPORT,
      gutter: { left: 12, right: 312 },
      cardHeight: 120,
    })!;
    expect(narrow.width).toBe(300);
    expect(narrow.left).toBe(12);
  });

  it("clamps the card inside the viewport while the paragraph is visible", () => {
    // Paragraph top scrolled above the viewport: the card pins to the top edge.
    const above = placeCard({
      paraRects: [rect(640, -200, 500, 600)],
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 200,
    })!;
    expect(above.top).toBe(VIEWPORT.top + CARD_EDGE);

    // Paragraph near the bottom: the card's bottom stays inside the viewport.
    const below = placeCard({
      paraRects: [rect(640, 850, 500, 300)],
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 200,
    })!;
    expect(below.top + 200).toBe(VIEWPORT.bottom - CARD_EDGE);
  });

  it("caps maxHeight at the visible height and never overflows a tall card", () => {
    const p = placeCard({
      paraRects: [rect(640, 400, 500, 80)],
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 5000,
    })!;
    expect(p.maxHeight).toBe(800 - 2 * CARD_EDGE);
    expect(p.top).toBe(VIEWPORT.top + CARD_EDGE);
    expect(p.top + p.maxHeight).toBeLessThanOrEqual(VIEWPORT.bottom);
  });

  it("points the arrow at the visible middle of the paragraph", () => {
    const p = placeCard({
      paraRects: [rect(640, 300, 500, 80)],
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 200,
    })!;
    // Middle of the paragraph is y=340; the card top is 300.
    expect(p.top + p.arrowY + 5).toBe(340);
  });

  it("returns null once no fragment is in view", () => {
    expect(
      placeCard({
        paraRects: [rect(640, 950, 500, 80)],
        viewport: VIEWPORT,
        gutter: WIDE,
        cardHeight: 100,
      }),
    ).toBeNull();
  });
});

describe("placeCard — scroll fraction", () => {
  const at = (top: number, height = 1000) =>
    placeCard({
      paraRects: [rect(640, top, 500, height)],
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 300,
    })!.scrollFraction;

  it("is the fraction of the paragraph scrolled past the viewport top", () => {
    expect(at(400)).toBe(0); // not reached the top yet
    expect(at(100)).toBe(0); // exactly at the top
    expect(at(-150)).toBe(0.25);
    expect(at(-400)).toBe(0.5);
    expect(at(-850)).toBe(0.95);
  });

  it("maps the fraction into the active fragment's span of the content", () => {
    // No divider: the whole scroll range.
    expect(contentScrollTop({ fraction: 0.5, activeFragment: 0, breaks: [], range: 400 })).toBe(
      200,
    );
    // Divider at 300px of 500px range: fragment 0 spans 0-300, fragment 1 300-500.
    expect(
      contentScrollTop({ fraction: 0.5, activeFragment: 0, breaks: [300], range: 500 }),
    ).toBe(150);
    expect(
      contentScrollTop({ fraction: 0, activeFragment: 1, breaks: [300], range: 500 }),
    ).toBe(300);
    expect(
      contentScrollTop({ fraction: 1, activeFragment: 1, breaks: [300], range: 500 }),
    ).toBe(500);
    // A divider past the scroll range is clamped to it.
    expect(
      contentScrollTop({ fraction: 0.5, activeFragment: 1, breaks: [900], range: 500 }),
    ).toBe(500);
    // Nothing to scroll.
    expect(contentScrollTop({ fraction: 0.7, activeFragment: 0, breaks: [], range: 0 })).toBe(0);
  });
});

describe("placeCard — split paragraphs", () => {
  // Fragment 0 ends a page; fragment 1 starts the next page 40px further down.
  const frags = (scroll: number) => [
    rect(640, 600 - scroll, 500, 280),
    rect(640, 920 - scroll, 500, 200),
  ];

  it("tracks the fragment most in view, and the arrow follows the second once the first scrolls out", () => {
    const first = placeCard({
      paraRects: frags(0),
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 150,
    })!;
    expect(first.activeFragment).toBe(0);

    const second = placeCard({
      paraRects: frags(700), // fragment 0 is now at -100..180, fragment 1 at 220..420
      viewport: VIEWPORT,
      gutter: WIDE,
      cardHeight: 150,
    })!;
    expect(second.activeFragment).toBe(1);
    expect(second.top).toBe(220);
    // Arrow at fragment 1's middle (y=320), not fragment 0's.
    expect(second.top + second.arrowY + 5).toBe(320);
  });

  it("describes a break by the next fragment's page and x position", () => {
    expect(
      describeBreak({ page: 0, bbox: [50, 600, 290, 760] }, { page: 1, bbox: [50, 40, 290, 90] }),
    ).toEqual({ kind: "page", page: 2 });
    expect(
      describeBreak({ page: 2, bbox: [50, 600, 290, 760] }, { page: 2, bbox: [310, 60, 550, 90] }),
    ).toEqual({ kind: "column" });
    // Past the current fragment's horizontal midpoint also counts as a column.
    expect(
      describeBreak({ page: 2, bbox: [50, 600, 290, 760] }, { page: 2, bbox: [200, 60, 550, 90] }),
    ).toEqual({ kind: "column" });
  });

  it("leaves a same-page, same-column bridge (equation, header) unlabelled", () => {
    // The paragraph resumes below a display equation in the same column.
    expect(
      describeBreak({ page: 2, bbox: [50, 100, 290, 200] }, { page: 2, bbox: [52, 260, 290, 320] }),
    ).toEqual({ kind: "none" });
    // Same column, even slightly indented, is still not a column break.
    expect(
      describeBreak({ page: 2, bbox: [50, 100, 290, 200] }, { page: 2, bbox: [120, 260, 290, 320] }),
    ).toEqual({ kind: "none" });
  });
});

describe("placeCard — narrow fallback", () => {
  const place = (gutterWidth: number) =>
    placeCard({
      paraRects: [rect(640, 300, 500, 80)],
      viewport: VIEWPORT,
      gutter: { left: 12, right: 12 + gutterWidth },
      cardHeight: 600,
      reader: READER,
    })!;

  it(`keeps the side card at a ${NARROW_GUTTER_PX}px gutter`, () => {
    expect(place(260).side).toBe("left");
  });

  it("drops below the paragraph inside the reader when the gutter is 259px", () => {
    const p = place(259);
    expect(p.side).toBe("below");
    expect(p.maxHeight).toBeLessThanOrEqual(0.45 * 800);
    expect(p.top).toBe(380 + 10); // under the paragraph's bottom
    expect(p.left).toBeGreaterThanOrEqual(READER.left);
    expect(p.left + p.width).toBeLessThanOrEqual(READER.right);
    expect(p.arrowY).toBe(0); // arrow on the top edge, pointing up
    expect(p.arrowX).toBeGreaterThan(0);
  });

  it("caps the below card at 460px so the arrow bounds match its width", () => {
    const p = placeCard({
      paraRects: [rect(620, 300, 660, 80)],
      viewport: VIEWPORT,
      gutter: { left: 12, right: 200 },
      cardHeight: 200,
      reader: READER,
    })!;
    expect(p.width).toBe(460);
    expect(p.arrowX).toBeLessThanOrEqual(460 - 30);
  });

  it("lifts a below card that would run off the viewport bottom", () => {
    const p = placeCard({
      paraRects: [rect(640, 700, 500, 150)],
      viewport: VIEWPORT,
      gutter: { left: 12, right: 200 },
      cardHeight: 300,
      reader: READER,
    })!;
    expect(p.top + Math.min(300, p.maxHeight)).toBe(VIEWPORT.bottom - CARD_EDGE);
  });
});
