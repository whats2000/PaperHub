import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/** A drawing point in the svg's user units, before the view transform. */
export type DrawingPoint = readonly [number, number];

/** drawing point p is shown at viewBox point `p * k + (x, y)`. */
export interface ViewTransform {
  x: number;
  y: number;
  k: number;
}

export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A client-px rectangle. */
export interface ClientBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * An overlay that covers part of the drawing (the legend): `box` in client
 * px, and the drawn items that must not end up under it as [corner, corner]
 * pairs in drawing units.
 */
export interface Occluder {
  box: ClientBox;
  points: readonly DrawingPoint[];
}

export interface PanZoomOptions {
  min?: number;
  max?: number;
}

export interface PanZoom {
  transform: ViewTransform;
  /** The last change came from a continuous gesture (drag, wheel): render it without a transition. */
  live: boolean;
  /** Zoom by `factor` around a client point (default: the svg's centre). */
  zoomAt: (factor: number, clientPoint?: { x: number; y: number }) => void;
  /** Pan by a client-pixel offset. */
  panBy: (dx: number, dy: number) => void;
  /**
   * Frame `bounds` (drawing units) left of `rightInset` (client px); with no
   * bounds, reset to 100 %. With an occluder, the whole area is used unless
   * that would put a drawn item under it; then the drawing is framed beside or
   * above it, whichever frames larger. `instant` skips the glide (a drawing's
   * first frame). Returns the transform it applied.
   */
  fit: (bounds?: Bounds, rightInset?: number, instant?: boolean, occluder?: Occluder) => ViewTransform;
  /**
   * Pan the least needed to keep `points` clear of the edges and of a
   * right-side inset (client px), and their [corner, corner] boxes out from
   * under `avoid` (client px).
   */
  panToKeepVisible: (points: readonly DrawingPoint[], rightInset: number, avoid?: ClientBox) => void;
  /** The last pointer gesture moved more than the drag threshold. */
  wasDrag: () => boolean;
}

/** Movement (client px) between pointer down and up beyond which a press is a drag. */
const DRAG_PX = 4;
/** Clearance (client px) panToKeepVisible keeps from each edge. */
const EDGE_PX = 24;
const WHEEL_STEP = 1.12;
const DBLCLICK_STEP = 1.5;
const FIT_PAD = 0.92;
const IDENTITY: ViewTransform = { x: 0, y: 0, k: 1 };

interface Geometry {
  rect: DOMRect;
  vx: number;
  vy: number;
  vw: number;
  vh: number;
  /** Client px per viewBox unit (preserveAspectRatio "xMidYMid meet"). */
  s: number;
  /** Client position of the viewBox origin. */
  ox: number;
  oy: number;
}

function geometry(svg: SVGSVGElement): Geometry {
  const rect = svg.getBoundingClientRect();
  const parts = (svg.getAttribute("viewBox") ?? "").trim().split(/[\s,]+/).map(Number);
  const hasBox = parts.length === 4 && parts.every(Number.isFinite) && parts[2]! > 0 && parts[3]! > 0;
  const [vx, vy, vw, vh] = hasBox ? (parts as [number, number, number, number]) : [0, 0, rect.width || 1, rect.height || 1];
  const sized = rect.width > 0 && rect.height > 0;
  const s = sized ? Math.min(rect.width / vw, rect.height / vh) : 1;
  return {
    rect,
    vx,
    vy,
    vw,
    vh,
    s,
    ox: rect.left + (sized ? (rect.width - vw * s) / 2 : 0),
    oy: rect.top + (sized ? (rect.height - vh * s) / 2 : 0),
  };
}

/** Client rects of [corner, corner] point pairs under transform t. */
function pairRects(points: readonly DrawingPoint[], t: ViewTransform, g: Geometry): ClientBox[] {
  const out: ClientBox[] = [];
  for (let i = 0; i + 1 < points.length; i += 2) {
    const [ax, ay] = points[i]!;
    const [bx, by] = points[i + 1]!;
    const x0 = g.ox + (Math.min(ax, bx) * t.k + t.x - g.vx) * g.s;
    const x1 = g.ox + (Math.max(ax, bx) * t.k + t.x - g.vx) * g.s;
    const y0 = g.oy + (Math.min(ay, by) * t.k + t.y - g.vy) * g.s;
    const y1 = g.oy + (Math.max(ay, by) * t.k + t.y - g.vy) * g.s;
    out.push({ x0, y0, x1, y1 });
  }
  return out;
}

const clientOverlap = (a: ClientBox, b: ClientBox) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/** Distance to move a span [lo, hi] into [min, max]; a span that can't fit is centred. */
function shiftInto(lo: number, hi: number, min: number, max: number): number {
  if (hi - lo > max - min) return (min + max) / 2 - (lo + hi) / 2;
  if (hi > max) return max - hi;
  if (lo < min) return min - lo;
  return 0;
}

/**
 * Pan and zoom for an svg drawing. Wheel zooms around the cursor, a drag pans
 * (and is reported by `wasDrag()` so the click that ends it can be ignored),
 * a double-click on empty space zooms in at the pointer. Render `transform`
 * on the drawing's root group.
 */
export function usePanZoom(svgRef: RefObject<SVGSVGElement | null>, { min = 0.5, max = 3 }: PanZoomOptions = {}): PanZoom {
  const [view, setView] = useState<{ t: ViewTransform; live: boolean }>({ t: IDENTITY, live: false });
  // Mirror for handlers that run several times between renders.
  const tRef = useRef<ViewTransform>(IDENTITY);
  const dragged = useRef(false);

  const commit = useCallback((t: ViewTransform, live: boolean) => {
    tRef.current = t;
    setView({ t, live });
  }, []);

  const clamp = useCallback((k: number) => Math.min(max, Math.max(min, k)), [min, max]);

  const zoomAtImpl = useCallback(
    (factor: number, clientPoint: { x: number; y: number } | undefined, live: boolean) => {
      const svg = svgRef.current;
      const t = tRef.current;
      const k = clamp(t.k * factor);
      if (k === t.k) return;
      let px: number;
      let py: number;
      if (svg) {
        const g = geometry(svg);
        px = clientPoint ? (clientPoint.x - g.ox) / g.s + g.vx : g.vx + g.vw / 2;
        py = clientPoint ? (clientPoint.y - g.oy) / g.s + g.vy : g.vy + g.vh / 2;
      } else {
        px = clientPoint?.x ?? 0;
        py = clientPoint?.y ?? 0;
      }
      const f = k / t.k;
      commit({ x: px - (px - t.x) * f, y: py - (py - t.y) * f, k }, live);
    },
    [svgRef, clamp, commit],
  );

  const panByImpl = useCallback(
    (dx: number, dy: number, live: boolean) => {
      const svg = svgRef.current;
      const s = svg ? geometry(svg).s : 1;
      const t = tRef.current;
      commit({ ...t, x: t.x + dx / s, y: t.y + dy / s }, live);
    },
    [svgRef, commit],
  );

  const zoomAt = useCallback(
    (factor: number, clientPoint?: { x: number; y: number }) => zoomAtImpl(factor, clientPoint, false),
    [zoomAtImpl],
  );
  const panBy = useCallback((dx: number, dy: number) => panByImpl(dx, dy, false), [panByImpl]);

  const fit = useCallback(
    (bounds?: Bounds, rightInset = 0, instant = false, occluder?: Occluder): ViewTransform => {
      const svg = svgRef.current;
      if (!bounds || !svg || bounds.width <= 0 || bounds.height <= 0) {
        commit(IDENTITY, false);
        return IDENTITY;
      }
      const g = geometry(svg);
      // Frame the bounds in a client-px area (left, top, right, bottom).
      const frame = (l: number, top: number, r: number, b: number): ViewTransform => {
        // The area in viewBox units.
        const x0 = (l - g.ox) / g.s + g.vx;
        const y0 = (top - g.oy) / g.s + g.vy;
        const w = Math.max(1, (r - l) / g.s);
        const h = Math.max(1, (b - top) / g.s);
        const k = clamp(Math.min(w / bounds.width, h / bounds.height) * FIT_PAD);
        return {
          k,
          x: x0 + w / 2 - (bounds.x + bounds.width / 2) * k,
          y: y0 + h / 2 - (bounds.y + bounds.height / 2) * k,
        };
      };
      let t: ViewTransform;
      if (g.rect.width > 0 && g.rect.height > 0) {
        const { left, top } = g.rect;
        const right = left + g.rect.width - rightInset;
        const bottom = top + g.rect.height;
        t = frame(left, top, right, bottom);
        const box = occluder?.box;
        if (box && pairRects(occluder.points, t, g).some((r) => clientOverlap(r, box))) {
          // Beside the occluder or above it, whichever leaves a usable area and frames larger.
          const options: ViewTransform[] = [];
          if (right - box.x1 > g.rect.width / 3) options.push(frame(box.x1, top, right, bottom));
          if (box.y0 - top > g.rect.height / 3) options.push(frame(left, top, right, box.y0));
          if (options.length) t = options.reduce((a, b) => (b.k > a.k ? b : a));
        }
      } else {
        t = frame(g.ox, g.oy, g.ox + g.vw * g.s, g.oy + g.vh * g.s);
      }
      commit(t, instant);
      return t;
    },
    [svgRef, clamp, commit],
  );

  const panToKeepVisible = useCallback(
    (points: readonly DrawingPoint[], rightInset: number, avoid?: ClientBox) => {
      const svg = svgRef.current;
      if (!svg || points.length === 0) return;
      const g = geometry(svg);
      if (g.rect.width <= 0 || g.rect.height <= 0) return;
      const t = tRef.current;
      const cx = points.map(([x]) => g.ox + (x * t.k + t.x - g.vx) * g.s);
      const cy = points.map(([, y]) => g.oy + (y * t.k + t.y - g.vy) * g.s);
      let sx = shiftInto(
        Math.min(...cx),
        Math.max(...cx),
        g.rect.left + EDGE_PX,
        g.rect.left + g.rect.width - rightInset - EDGE_PX,
      );
      const top = g.rect.top + EDGE_PX;
      const bottom = g.rect.top + g.rect.height - EDGE_PX;
      const right = g.rect.left + g.rect.width - rightInset - EDGE_PX;
      let sy = shiftInto(Math.min(...cy), Math.max(...cy), top, bottom);
      if (avoid) {
        // Out from under the overlay too: right of it or above it, whichever is the shorter move that stays in view.
        const hit = pairRects(points, t, g)
          .map((r) => ({ x0: r.x0 + sx, y0: r.y0 + sy, x1: r.x1 + sx, y1: r.y1 + sy }))
          .filter((r) => clientOverlap(r, avoid));
        if (hit.length > 0) {
          const dx = avoid.x1 + EDGE_PX - Math.min(...hit.map((r) => r.x0));
          const dy = avoid.y0 - EDGE_PX - Math.max(...hit.map((r) => r.y1));
          const moves = [
            { x: dx, y: 0, ok: Math.max(...cx) + sx + dx <= right },
            { x: 0, y: dy, ok: Math.min(...cy) + sy + dy >= top },
          ].filter((m) => m.ok);
          const m = moves.sort((a, b) => Math.abs(a.x + a.y) - Math.abs(b.x + b.y))[0];
          if (m) {
            sx += m.x;
            sy += m.y;
          }
        }
      }
      if (sx === 0 && sy === 0) return;
      commit({ ...t, x: t.x + sx / g.s, y: t.y + sy / g.s }, false);
    },
    [svgRef, commit],
  );

  const wasDrag = useCallback(() => dragged.current, []);

  // Bind the gesture listeners to whichever svg the ref holds (it mounts late
  // and is replaced when the graph reloads); rebinding only when it changes.
  const bound = useRef<{ el: SVGSVGElement; off: () => void } | null>(null);
  useEffect(() => {
    const el = svgRef.current;
    if (bound.current?.el === el) return;
    bound.current?.off();
    bound.current = null;
    if (!el) return;

    let press: { id: number; x0: number; y0: number; x: number; y: number } | null = null;
    let resetTimer: ReturnType<typeof setTimeout> | null = null;
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      dragged.current = false;
      press = { id: e.pointerId, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY };
    };
    const onMove = (e: PointerEvent) => {
      if (!press || e.pointerId !== press.id) return;
      if (!dragged.current) {
        if (Math.hypot(e.clientX - press.x0, e.clientY - press.y0) <= DRAG_PX) return;
        dragged.current = true;
        try {
          el.setPointerCapture(e.pointerId);
        } catch {
          // A synthetic or already-released pointer can't be captured; panning still works.
        }
      }
      panByImpl(e.clientX - press.x, e.clientY - press.y, true);
      press.x = e.clientX;
      press.y = e.clientY;
    };
    const onUp = (e: PointerEvent) => {
      if (!press || e.pointerId !== press.id) return;
      press = null;
      // The click that ends a pan follows in this same input task; once it has
      // been ignored, a later click (e.g. an assistive tech's virtual one) counts.
      if (dragged.current) {
        if (resetTimer !== null) clearTimeout(resetTimer);
        resetTimer = setTimeout(() => {
          resetTimer = null;
          dragged.current = false;
        }, 0);
      }
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoomAtImpl(e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP, { x: e.clientX, y: e.clientY }, true);
    };
    const onDblClick = (e: MouseEvent) => {
      // Double-clicking a node, an idea or its badge is two selections, not a zoom.
      if (e.target instanceof Element && e.target.closest('[role="button"], [data-graph-item]')) return;
      zoomAtImpl(DBLCLICK_STEP, { x: e.clientX, y: e.clientY }, false);
    };
    // A keyboard activation is never the end of a drag.
    const onKey = () => {
      dragged.current = false;
    };

    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("dblclick", onDblClick);
    el.addEventListener("keydown", onKey, true);
    bound.current = {
      el,
      off: () => {
        if (resetTimer !== null) clearTimeout(resetTimer);
        el.removeEventListener("pointerdown", onDown);
        el.removeEventListener("pointermove", onMove);
        el.removeEventListener("pointerup", onUp);
        el.removeEventListener("pointercancel", onUp);
        el.removeEventListener("wheel", onWheel);
        el.removeEventListener("dblclick", onDblClick);
        el.removeEventListener("keydown", onKey, true);
      },
    };
  });
  useEffect(
    () => () => {
      bound.current?.off();
      bound.current = null;
    },
    [],
  );

  return { transform: view.t, live: view.live, zoomAt, panBy, fit, panToKeepVisible, wasDrag };
}
