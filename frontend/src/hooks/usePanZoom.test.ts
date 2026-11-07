import { act, fireEvent, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePanZoom, type ViewTransform } from "./usePanZoom";

const SVG_NS = "http://www.w3.org/2000/svg";

/** A 900×600 svg whose viewBox matches its box, so 1 client px = 1 unit. */
function makeSvg(): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 900 600");
  svg.getBoundingClientRect = () =>
    ({ left: 0, top: 0, right: 900, bottom: 600, width: 900, height: 600, x: 0, y: 0, toJSON: () => ({}) });
  document.body.appendChild(svg);
  return svg;
}

/** The drawing point under a client point, given the current transform. */
const graphPoint = (t: ViewTransform, p: { x: number; y: number }) => ({ x: (p.x - t.x) / t.k, y: (p.y - t.y) / t.k });

describe("usePanZoom", () => {
  let svg: SVGSVGElement;
  beforeEach(() => {
    svg = makeSvg();
  });
  afterEach(() => svg.remove());

  const setup = () => renderHook(() => usePanZoom({ current: svg }, { min: 0.5, max: 3 }));

  it("starts at the identity transform", () => {
    const { result } = setup();
    expect(result.current.transform).toEqual({ x: 0, y: 0, k: 1 });
  });

  it("clamps zoom at 0.5 and 3", () => {
    const { result } = setup();
    act(() => result.current.zoomAt(10, { x: 450, y: 300 }));
    expect(result.current.transform.k).toBe(3);
    act(() => result.current.zoomAt(1.5, { x: 450, y: 300 }));
    expect(result.current.transform.k).toBe(3);
    act(() => result.current.zoomAt(0.001, { x: 450, y: 300 }));
    expect(result.current.transform.k).toBe(0.5);
  });

  it("keeps the cursor's graph point fixed while zooming", () => {
    const { result } = setup();
    const cursor = { x: 300, y: 200 };
    const before = graphPoint(result.current.transform, cursor);
    act(() => result.current.zoomAt(2, cursor));
    expect(graphPoint(result.current.transform, cursor)).toEqual(before);
    const other = { x: 700, y: 90 };
    const before2 = graphPoint(result.current.transform, other);
    act(() => result.current.zoomAt(0.7, other));
    const after2 = graphPoint(result.current.transform, other);
    expect(after2.x).toBeCloseTo(before2.x, 6);
    expect(after2.y).toBeCloseTo(before2.y, 6);
  });

  it("zooms around the wheel cursor", () => {
    const { result } = setup();
    const cursor = { x: 600, y: 400 };
    act(() => {
      fireEvent.wheel(svg, { deltaY: -100, clientX: cursor.x, clientY: cursor.y });
    });
    expect(result.current.transform.k).toBeGreaterThan(1);
    const g = graphPoint(result.current.transform, cursor);
    expect(g.x).toBeCloseTo(600, 6);
    expect(g.y).toBeCloseTo(400, 6);
  });

  it("double-click zooms in by 1.5 at the pointer", () => {
    const { result } = setup();
    act(() => {
      fireEvent.doubleClick(svg, { clientX: 200, clientY: 100 });
    });
    expect(result.current.transform.k).toBeCloseTo(1.5, 6);
    const g = graphPoint(result.current.transform, { x: 200, y: 100 });
    expect(g.x).toBeCloseTo(200, 6);
  });

  it("a 10 px drag pans and counts as a drag", () => {
    const { result } = setup();
    act(() => {
      fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
      fireEvent.pointerMove(svg, { pointerId: 1, clientX: 110, clientY: 100 });
      fireEvent.pointerUp(svg, { pointerId: 1, clientX: 110, clientY: 100 });
    });
    expect(result.current.wasDrag()).toBe(true);
    expect(result.current.transform.x).toBeCloseTo(10, 6);
  });

  it("a 3 px jitter is a click, not a drag", () => {
    const { result } = setup();
    act(() => {
      fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
      fireEvent.pointerMove(svg, { pointerId: 1, clientX: 103, clientY: 100 });
      fireEvent.pointerUp(svg, { pointerId: 1, clientX: 103, clientY: 100 });
    });
    expect(result.current.wasDrag()).toBe(false);
    expect(result.current.transform).toEqual({ x: 0, y: 0, k: 1 });
  });

  it("a keyboard activation after a drag is not treated as a drag", () => {
    const { result } = setup();
    act(() => {
      fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
      fireEvent.pointerMove(svg, { pointerId: 1, clientX: 140, clientY: 100 });
      fireEvent.pointerUp(svg, { pointerId: 1, clientX: 140, clientY: 100 });
    });
    expect(result.current.wasDrag()).toBe(true);
    fireEvent.keyDown(svg, { key: "Enter" });
    expect(result.current.wasDrag()).toBe(false);
  });

  it("pans points out from under a right-side inset", () => {
    const { result } = setup();
    act(() => result.current.panToKeepVisible([[700, 300], [820, 320]], 340));
    const t = result.current.transform;
    // Both points now sit left of the inset (900 - 340 = 560) with a margin.
    for (const x of [700, 820]) expect(x * t.k + t.x).toBeLessThanOrEqual(560);
    expect(t.k).toBe(1);
  });

  it("leaves the view alone when the points are already clear", () => {
    const { result } = setup();
    act(() => result.current.panToKeepVisible([[200, 300], [300, 320]], 340));
    expect(result.current.transform).toEqual({ x: 0, y: 0, k: 1 });
  });

  it("fit() frames the given bounds", () => {
    const { result } = setup();
    act(() => result.current.zoomAt(2, { x: 0, y: 0 }));
    act(() => {
      result.current.fit({ x: 0, y: 0, width: 900, height: 600 });
    });
    const t = result.current.transform;
    // The bounds' centre lands at the viewBox centre.
    expect(450 * t.k + t.x).toBeCloseTo(450, 6);
    expect(300 * t.k + t.y).toBeCloseTo(300, 6);
    act(() => {
      result.current.fit();
    });
    expect(result.current.transform).toEqual({ x: 0, y: 0, k: 1 });
  });
  it("fit() frames the bounds left of a right inset", () => {
    const { result } = setup();
    act(() => {
      result.current.fit({ x: 0, y: 0, width: 900, height: 600 }, 340);
    });
    const t = result.current.transform;
    // Centred in the free 560 px: both edges stay left of the inset.
    expect(0 * t.k + t.x).toBeGreaterThanOrEqual(0);
    expect(900 * t.k + t.x).toBeLessThanOrEqual(560);
    expect((450 * t.k + t.x)).toBeCloseTo(280, 6);
  });
  it("fit() frames the bounds between a left-hand occluder and a right inset, and returns what it applied", () => {
    const { result } = setup();
    let applied: ViewTransform | undefined;
    act(() => {
      applied = result.current.fit({ x: 0, y: 0, width: 900, height: 600 }, 200, true, {
        box: { x0: 0, y0: 0, x1: 220, y1: 600 },
        points: [[10, 10], [50, 50]],
      });
    });
    const t = result.current.transform;
    expect(applied).toEqual(t);
    // Instant: no glide on a drawing's first frame.
    expect(result.current.live).toBe(true);
    expect(0 * t.k + t.x).toBeGreaterThanOrEqual(220);
    expect(900 * t.k + t.x).toBeLessThanOrEqual(700);
    expect(450 * t.k + t.x).toBeCloseTo(460, 6);
  });
  it("fit() ignores an occluder that would leave too little room", () => {
    const { result } = setup();
    act(() => {
      result.current.fit({ x: 0, y: 0, width: 900, height: 600 }, 340, false, {
        box: { x0: 0, y0: 0, x1: 400, y1: 600 },
        points: [[10, 10], [50, 50]],
      });
    });
    const t = result.current.transform;
    expect(result.current.live).toBe(false);
    expect(450 * t.k + t.x).toBeCloseTo(280, 6);
  });


  // The legend card, bottom-left over the graph (client px).
  const LEGEND = { x0: 0, y0: 400, x1: 206, y1: 600 };
  /** The client rect of a [corner, corner] pair under transform t (1 client px = 1 unit here). */
  type Pair = [[number, number], [number, number]];
  const shown = (t: ViewTransform, [[ax, ay], [bx, by]]: Pair) => ({
    x0: ax * t.k + t.x,
    y0: ay * t.k + t.y,
    x1: bx * t.k + t.x,
    y1: by * t.k + t.y,
  });
  const overlaps = (a: typeof LEGEND, b: typeof LEGEND) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

  it("fit() uses the whole area when nothing drawn would land under the legend", () => {
    const { result } = setup();
    act(() => {
      result.current.fit({ x: 0, y: 0, width: 900, height: 600 }, 0, false, { box: LEGEND, points: [[400, 100], [500, 200]] });
    });
    expect(result.current.transform.k).toBeCloseTo(0.92, 6);
  });

  it("fit() frames the drawing beside the legend when it would cover a node", () => {
    const { result } = setup();
    const node: Pair = [[20, 500], [60, 560]];
    act(() => {
      result.current.fit({ x: 0, y: 0, width: 900, height: 600 }, 0, false, { box: LEGEND, points: node });
    });
    const t = result.current.transform;
    expect(overlaps(shown(t, node), LEGEND)).toBe(false);
    // Beside it (694 px wide) frames larger than above it (400 px tall).
    expect(t.k).toBeCloseTo((694 / 900) * 0.92, 6);
  });

  it("pans a selected node out from under the legend, the shorter way", () => {
    const { result } = setup();
    const node: Pair = [[50, 450], [150, 520]];
    act(() => result.current.panToKeepVisible(node, 340, LEGEND));
    const t = result.current.transform;
    expect(t.k).toBe(1);
    expect(overlaps(shown(t, node), LEGEND)).toBe(false);
    // Up by 144 px (to 24 px above the legend), not right by 180 px.
    expect(t.x).toBe(0);
    expect(t.y).toBeCloseTo(-144, 6);
  });

  it("leaves a selection beside the legend where it is", () => {
    const { result } = setup();
    act(() => result.current.panToKeepVisible([[300, 450], [400, 520]], 340, LEGEND));
    expect(result.current.transform).toEqual({ x: 0, y: 0, k: 1 });
  });
  it("forgets the drag once its click has been handled", () => {
    vi.useFakeTimers();
    try {
      const { result } = setup();
      act(() => {
        fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
        fireEvent.pointerMove(svg, { pointerId: 1, clientX: 140, clientY: 100 });
        fireEvent.pointerUp(svg, { pointerId: 1, clientX: 140, clientY: 100 });
      });
      // The click that ends the pan arrives in the same task and sees the drag...
      expect(result.current.wasDrag()).toBe(true);
      act(() => {
        vi.advanceTimersByTime(0);
      });
      // ...a later (e.g. assistive-tech) click does not.
      expect(result.current.wasDrag()).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("double-clicking a graph item or its badge doesn't zoom", () => {
    const { result } = setup();
    const badge = document.createElementNS(SVG_NS, "g");
    badge.setAttribute("data-graph-item", "");
    svg.appendChild(badge);
    act(() => {
      fireEvent.doubleClick(badge, { clientX: 200, clientY: 100 });
    });
    expect(result.current.transform.k).toBe(1);
  });
});
