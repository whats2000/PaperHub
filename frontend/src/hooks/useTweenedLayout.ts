import { useEffect, useRef, useState } from "react";

import type { GraphLayout, LayoutHull, LayoutNode } from "@/lib/ideaGraphLayout";
import { prefersReducedMotion } from "@/lib/motion";

export const TWEEN_MS = 250;

const easeOut = (t: number) => 1 - (1 - t) ** 3;

/** One frame between two layouts: shared nodes glide, new ones appear in place. */
export function interpolateLayout(
  from: GraphLayout,
  to: GraphLayout,
  t: number,
  hullsFor: (nodes: readonly LayoutNode[]) => LayoutHull[],
): GraphLayout {
  const prev = new Map(from.nodes.map((n) => [n.id, n]));
  const nodes = to.nodes.map((n) => {
    const p = prev.get(n.id);
    if (!p) return n;
    return { id: n.id, x: p.x + (n.x - p.x) * t, y: p.y + (n.y - p.y) * t, r: p.r + (n.r - p.r) * t };
  });
  return { nodes, hulls: hullsFor(nodes), problem: to.problem };
}

/**
 * Animate the graph from the layout on screen to a new one (~250 ms ease-out),
 * so nodes glide instead of jumping while ideas stream in. Edges and hulls are
 * drawn from the tweened positions, so they stay attached. Reduced motion
 * shows the new layout at once.
 */
export function useTweenedLayout(
  layout: GraphLayout,
  hullsFor: (nodes: readonly LayoutNode[]) => LayoutHull[],
  durationMs = TWEEN_MS,
): GraphLayout {
  const [target, setTarget] = useState(layout);
  const [display, setDisplay] = useState(layout);
  // Derive-during-render: adopt the new target without an effect round-trip.
  if (layout !== target) {
    setTarget(layout);
    if (prefersReducedMotion()) setDisplay(layout);
  }
  const shown = useRef(display);
  useEffect(() => {
    shown.current = display;
  }, [display]);

  useEffect(() => {
    const from = shown.current;
    if (from === target) return;
    // Time from the first frame's own timestamp: rAF's clock need not match
    // performance.now()'s origin.
    let start: number | null = null;
    let raf = 0;
    const step = (now: number) => {
      start ??= now;
      const t = Math.min(1, Math.max(0, (now - start) / durationMs));
      setDisplay(t >= 1 ? target : interpolateLayout(from, target, easeOut(t), hullsFor));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, hullsFor, durationMs]);

  return display;
}
