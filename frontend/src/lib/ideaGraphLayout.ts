// Ideas graph layout (Plan H3): a seeded, synchronous d3-force run that clusters
// documents by domain, plus padded convex hulls per domain.
import {
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationNodeDatum,
} from "d3-force";
import { polygonHull } from "d3-polygon";

import type { DomainAdjacency } from "@/lib/domainColors";
import type { GraphResponse } from "@/types/ideation";

export type Point = [number, number];

export interface LayoutNode {
  id: number;
  x: number;
  y: number;
  r: number;
}

export interface LayoutHull {
  domainId: number;
  points: Point[];
  /** Where the domain name goes (centre x, baseline y) and the box it covers. */
  label?: { x: number; y: number; box: Box };
}

export interface GraphLayout {
  nodes: LayoutNode[];
  hulls: LayoutHull[];
  /** Centre of the problem node, or null in free exploration. */
  problem: { x: number; y: number } | null;
}

export interface LayoutOptions {
  width: number;
  height: number;
  seed: number;
  /**
   * Warm start: documents found here start at these positions (no jitter), so
   * a small change (an idea count) nudges the layout instead of reshuffling it.
   */
  initial?: ReadonlyMap<number, { x: number; y: number }>;
  /** Ticks to run (default TICKS); a warm start needs fewer. */
  ticks?: number;
  /** Starting alpha (default 1); a warm start begins cooler. */
  alpha?: number;
}

/** Ticks and alpha for a warm-started relayout. */
export const WARM = { ticks: 80, alpha: 0.3 } as const;

export const TICKS = 300;
/** Half-extent of the problem node's rectangle, shared with the renderer. */
export const PROBLEM_HALF = { w: 56, h: 18 };
const HULL_PAD = 10;
const HULL_STEPS = 12;
/** Hulls this close (or overlapping) count as adjacent for colouring. */
export const ADJACENT_GAP = 24;

// --- label geometry, shared with the renderer ---
/** Characters a document's label keeps before the ellipsis (full title in the tooltip). */
export const LABEL_CHARS = 24;
/** Approximate advance (px) of one character of an 11 px label. */
const LABEL_CHAR_W = 6.2;
/** The label's baseline sits this far below the node's edge; its box ends LABEL_H below the edge. */
export const LABEL_DY = 13;
const LABEL_H = 17;
/** Clearance (px) kept between two nodes' boxes (circle + label). */
export const NODE_GAP = 6;
/** Approximate advance (px) of one character of an 11.5 px semibold hull label. */
const HULL_CHAR_W = 6.9;
const HULL_LABEL_H = 15;
/** Rows outward from a hull edge a label may move to find a free spot. */
const HULL_LABEL_ROWS = 6;
/** Further rows tried, beside the edge too, before a label settles for covering something. */
const HULL_LABEL_FAR_ROWS = 14;
/** Clearance (px) between two domain labels, so neighbours never read as one name. */
const HULL_LABEL_GAP = 12;
/** Room (px) above a node for its role tag ("Source"). */
const ROLE_ABOVE = 18;
/** Anchor ellipse radii as a fraction of the canvas width / height. */
const RING = { x: 0.36, y: 0.34 };

export function truncateLabel(s: string, n = LABEL_CHARS): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Approximate rendered width (px) of a document's (truncated) label. */
export function labelWidth(title: string): number {
  return Math.ceil(truncateLabel(title).length * LABEL_CHAR_W);
}

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** The box a document occupies: its circle and the label below it. */
export function nodeBox(n: { x: number; y: number; r: number }, title: string): Box {
  const hw = Math.max(n.r, labelWidth(title) / 2);
  return { x0: n.x - hw, y0: n.y - n.r, x1: n.x + hw, y1: n.y + n.r + LABEL_H };
}

/** The box of a document's label alone. */
export function labelBox(n: { x: number; y: number; r: number }, title: string): Box {
  const hw = labelWidth(title) / 2;
  return { x0: n.x - hw, y0: n.y + n.r + 2, x1: n.x + hw, y1: n.y + n.r + LABEL_H };
}

export function boxesOverlap(a: Box, b: Box, gap = 0): boolean {
  return a.x0 < b.x1 + gap && b.x0 < a.x1 + gap && a.y0 < b.y1 + gap && b.y0 < a.y1 + gap;
}

/** Node radius: size encodes the document's idea count. */
export function nodeRadius(ideaCount: number): number {
  return 8 + 3 * Math.sqrt(Math.max(0, ideaCount));
}

/** mulberry32: a small seeded PRNG in [0, 1). */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface SimNode extends SimulationNodeDatum {
  id: number;
  r: number;
  domains: number[];
  ax: number;
  ay: number;
  /** Box extents around (x, y): half width, height above, height below. */
  hw: number;
  up: number;
  down: number;
}

const round = (v: number) => Math.round(v * 100) / 100;

/**
 * Overlap of two nodes' boxes (with NODE_GAP clearance) as the push that
 * separates them along the cheaper axis, or null when they're clear.
 */
function boxPush(a: SimNode, b: SimNode, gap: number): { dx: number; dy: number } | null {
  const ax = a.x ?? 0;
  const ay = a.y ?? 0;
  const bx = b.x ?? 0;
  const by = b.y ?? 0;
  const ox = Math.min(ax + a.hw, bx + b.hw) - Math.max(ax - a.hw, bx - b.hw) + gap;
  const oy = Math.min(ay + a.down, by + b.down) - Math.max(ay - a.up, by - b.up) + gap;
  if (ox <= 0 || oy <= 0) return null;
  // Push along the axis that needs less movement; ties break by id, so it's deterministic.
  if (ox / (a.hw + b.hw) < oy / (a.up + a.down + b.up + b.down)) {
    const s = bx > ax || (bx === ax && b.id > a.id) ? 1 : -1;
    return { dx: s * ox, dy: 0 };
  }
  const s = by + (b.down - b.up) / 2 > ay + (a.down - a.up) / 2 || (by === ay && b.id > a.id) ? 1 : -1;
  return { dx: 0, dy: s * oy };
}

/** Share of a push each side takes: a pinned node (the problem) never moves. */
function shares(a: SimNode, b: SimNode): [number, number] {
  const aFixed = a.fx != null;
  const bFixed = b.fx != null;
  if (aFixed && bFixed) return [0, 0];
  if (aFixed) return [0, 1];
  if (bFixed) return [1, 0];
  return [0.5, 0.5];
}

/** Box collision (circle + label) as a velocity force, so labels make room too. */
function boxCollideForce(strength: number) {
  let nodes: SimNode[] = [];
  const force = () => {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]!;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j]!;
        const p = boxPush(a, b, NODE_GAP);
        if (!p) continue;
        const [sa, sb] = shares(a, b);
        a.vx = (a.vx ?? 0) - p.dx * sa * strength;
        a.vy = (a.vy ?? 0) - p.dy * sa * strength;
        b.vx = (b.vx ?? 0) + p.dx * sb * strength;
        b.vy = (b.vy ?? 0) + p.dy * sb * strength;
      }
    }
  };
  force.initialize = (n: SimNode[]) => {
    nodes = n;
  };
  return force;
}

/** Room (px) kept inside the canvas edge for the hull padding and the domain labels. */
const CANVAS_MARGIN = { x: 16, y: 36 };

/**
 * Keep every box inside the canvas: the charge pushes small, unlinked domains
 * far out, and a few stragglers shrink the whole fit.
 */
function containForce(width: number, height: number, strength: number) {
  let nodes: SimNode[] = [];
  const force = () => {
    for (const n of nodes) {
      if (n.fx != null) continue;
      const x = n.x ?? 0;
      const y = n.y ?? 0;
      const lo = { x: CANVAS_MARGIN.x + n.hw, y: CANVAS_MARGIN.y + n.up };
      const hi = { x: width - CANVAS_MARGIN.x - n.hw, y: height - CANVAS_MARGIN.y - n.down };
      if (x < lo.x) n.vx = (n.vx ?? 0) + (lo.x - x) * strength;
      else if (x > hi.x) n.vx = (n.vx ?? 0) + (hi.x - x) * strength;
      if (y < lo.y) n.vy = (n.vy ?? 0) + (lo.y - y) * strength;
      else if (y > hi.y) n.vy = (n.vy ?? 0) + (hi.y - y) * strength;
    }
  };
  force.initialize = (n: SimNode[]) => {
    nodes = n;
  };
  return force;
}

/** After the simulation: move overlapping boxes apart until none overlap (bounded passes). */
function separateBoxes(nodes: SimNode[], passes = 400): void {
  for (let pass = 0; pass < passes; pass++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]!;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j]!;
        // A little extra so rounding never leaves a hair of overlap.
        const p = boxPush(a, b, NODE_GAP + 0.5);
        if (!p) continue;
        const [sa, sb] = shares(a, b);
        if (sa === 0 && sb === 0) continue;
        a.x = (a.x ?? 0) - p.dx * sa;
        a.y = (a.y ?? 0) - p.dy * sa;
        b.x = (b.x ?? 0) + p.dx * sb;
        b.y = (b.y ?? 0) + p.dy * sb;
        moved = true;
      }
    }
    if (!moved) return;
  }
}

/**
 * Ring order for the domain anchors: greedily put the domain sharing the most
 * documents with the last one next to it, so a multi-domain document's anchors
 * are neighbours (its mean stays out on the ring, not in the middle).
 */
function ringOrder(populated: number[], docs: number[][]): number[] {
  if (populated.length <= 2) return populated;
  const shared = (a: number, b: number) => docs.filter((d) => d.includes(a) && d.includes(b)).length;
  const size = (a: number) => docs.filter((d) => d.includes(a)).length;
  const left = [...populated].sort((a, b) => size(b) - size(a) || a - b);
  const order = [left.shift()!];
  while (left.length) {
    const last = order[order.length - 1]!;
    let best = 0;
    for (let k = 1; k < left.length; k++) {
      const d = shared(last, left[k]!) - shared(last, left[best]!);
      if (d > 0 || (d === 0 && size(left[k]!) > size(left[best]!))) best = k;
    }
    order.push(left.splice(best, 1)[0]!);
  }
  return order;
}

/**
 * Pull each document toward the mean of its domains' current centroids, so a
 * multi-domain document settles between its clusters.
 */
function clusterForce(strength: number) {
  let nodes: SimNode[] = [];
  const force = (alpha: number) => {
    const sum = new Map<number, { x: number; y: number; n: number }>();
    for (const node of nodes) {
      for (const d of node.domains) {
        const s = sum.get(d) ?? { x: 0, y: 0, n: 0 };
        s.x += node.x ?? 0;
        s.y += node.y ?? 0;
        s.n += 1;
        sum.set(d, s);
      }
    }
    for (const node of nodes) {
      if (node.domains.length === 0) continue;
      let tx = 0;
      let ty = 0;
      for (const d of node.domains) {
        const s = sum.get(d)!;
        tx += s.x / s.n;
        ty += s.y / s.n;
      }
      tx /= node.domains.length;
      ty /= node.domains.length;
      node.vx = (node.vx ?? 0) + (tx - (node.x ?? 0)) * strength * alpha;
      node.vy = (node.vy ?? 0) + (ty - (node.y ?? 0)) * strength * alpha;
    }
  };
  force.initialize = (n: SimNode[]) => {
    nodes = n;
  };
  return force;
}

/** Convex hull of the nodes' circles padded by `r + HULL_PAD`. */
function paddedHull(members: LayoutNode[]): Point[] {
  const pts: Point[] = [];
  for (const n of members) {
    const pad = n.r + HULL_PAD;
    for (let k = 0; k < HULL_STEPS; k++) {
      const a = (2 * Math.PI * k) / HULL_STEPS;
      // Circumscribe the circle so the polygon never cuts inside the padding.
      const rr = pad / Math.cos(Math.PI / HULL_STEPS);
      pts.push([round(n.x + rr * Math.cos(a)), round(n.y + rr * Math.sin(a))]);
    }
  }
  return polygonHull(pts) ?? pts;
}

type LayoutInput = Pick<GraphResponse, "documents" | "domains" | "problem">;

/** Padded hull per populated domain, around the given node positions, each with a placed label. */
export function domainHulls(
  graph: Pick<GraphResponse, "documents" | "domains">,
  nodes: readonly LayoutNode[],
  problem: { x: number; y: number } | null = null,
): LayoutHull[] {
  const known = new Set(graph.domains.map((d) => d.id));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const populated = [...new Set(graph.documents.flatMap((d) => d.domains.filter((x) => known.has(x))))].sort(
    (a, b) => a - b,
  );
  const out: LayoutHull[] = [];
  for (const domainId of populated) {
    const members = graph.documents
      .filter((d) => d.domains.includes(domainId))
      .map((d) => byId.get(d.id))
      .filter((n): n is LayoutNode => n !== undefined);
    if (members.length) out.push({ domainId, points: paddedHull(members) });
  }
  // Obstacles a hull label must not cover: every document's circle and its label, and the
  // problem. The two boxes, not their union: the space beside a circle is free.
  const title = new Map(graph.documents.map((d) => [d.id, d.title]));
  const obstacles: Box[] = nodes.flatMap((n) => [
    { x0: n.x - n.r, y0: n.y - n.r, x1: n.x + n.r, y1: n.y + n.r },
    labelBox(n, title.get(n.id) ?? ""),
  ]);
  if (problem) obstacles.push(problemBox(problem));
  const names = new Map(graph.domains.map((d) => [d.id, d.name]));
  placeHullLabels(out, names, obstacles);
  return out;
}

function problemBox(p: { x: number; y: number }): Box {
  return { x0: p.x - PROBLEM_HALF.w, y0: p.y - PROBLEM_HALF.h - ROLE_ABOVE, x1: p.x + PROBLEM_HALF.w, y1: p.y + PROBLEM_HALF.h };
}

/** Approximate box of a hull label of `name` whose baseline is at (x, y), centred on x. */
export function hullLabelBox(name: string, x: number, y: number): Box {
  const hw = (name.length * HULL_CHAR_W) / 2;
  return { x0: x - hw, y0: y - HULL_LABEL_H + 4, x1: x + hw, y1: y + 4 };
}

/**
 * Put each hull's label just outside its edge, preferring the top centre, at
 * the first spot that covers no other label and no document. Smaller hulls
 * choose first (they have fewer spots). A label with no free spot takes the
 * spot that covers the fewest things.
 */
function placeHullLabels(hulls: LayoutHull[], names: ReadonlyMap<number, string>, obstacles: readonly Box[]): void {
  const extent = (h: LayoutHull) => {
    const xs = h.points.map((p) => p[0]);
    const ys = h.points.map((p) => p[1]);
    return Math.max(...xs) - Math.min(...xs) + Math.max(...ys) - Math.min(...ys);
  };
  const order = [...hulls].sort((a, b) => extent(a) - extent(b) || a.domainId - b.domainId);
  const placed: Box[] = [];
  for (const h of order) {
    const name = names.get(h.domainId) ?? "";
    const halfW = (name.length * HULL_CHAR_W) / 2;
    const xs = h.points.map((p) => p[0]);
    const ys = h.points.map((p) => p[1]);
    const midX = (Math.min(...xs) + Math.max(...xs)) / 2;
    const midY = (Math.min(...ys) + Math.max(...ys)) / 2;
    const topY = Math.min(...ys);
    // Spots on the edge: every vertex and every edge midpoint.
    const spots: Point[] = h.points.flatMap((p, i) => {
      const q = h.points[(i + 1) % h.points.length] ?? p;
      return [p, [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2] as Point];
    });
    // Above the edge on the upper half, below it on the lower half; when that's
    // taken, a row or a few further out, or slid half a label sideways. In a
    // dense core, further rows and spots beside the edge come next.
    const row = (py: number, k: number) => {
      const up = py <= midY;
      return up ? py - 6 - k * (HULL_LABEL_H + 2) : py + HULL_LABEL_H - 1 + k * (HULL_LABEL_H + 2);
    };
    const near = spots.flatMap(([px, py]) =>
      Array.from({ length: HULL_LABEL_ROWS }, (_, k) => {
        const score = py - topY + 0.35 * Math.abs(px - midX) + k * 90;
        return [0, -halfW, halfW].map((dx, s) => ({ x: px + dx, y: row(py, k), score: score + s * 30 }));
      }).flat(),
    );
    const far = spots.flatMap(([px, py]) =>
      Array.from({ length: HULL_LABEL_FAR_ROWS }, (_, k) => {
        const score = 10_000 + py - topY + 0.35 * Math.abs(px - midX) + k * 90;
        const side = px <= midX ? -1 : 1;
        // A row further out, or beside the edge (outward), at the spot's height.
        return [
          { x: px, y: row(py, HULL_LABEL_ROWS + k), score },
          { x: px + side * (halfW + 6 + k * 12), y: py + 5, score: score + 45 },
        ];
      }).flat(),
    );
    const byScore = (a: { x: number; y: number; score: number }, b: { x: number; y: number; score: number }) =>
      a.score - b.score || a.x - b.x || a.y - b.y;
    const candidates = [...near.sort(byScore), ...far.sort(byScore)];
    let best: { x: number; y: number; box: Box } | null = null;
    let bestHits = Infinity;
    for (const c of candidates) {
      const box = hullLabelBox(name, round(c.x), round(c.y));
      // Another domain's label is never covered, unless every spot covers one.
      const hits =
        placed.filter((b) => boxesOverlap(box, b, HULL_LABEL_GAP)).length * 1000 +
        obstacles.filter((b) => boxesOverlap(box, b)).length;
      if (hits < bestHits) {
        best = { x: round(c.x), y: round(c.y), box };
        bestHits = hits;
        if (hits === 0) break;
      }
    }
    if (best) {
      h.label = best;
      placed.push(best.box);
    }
  }
}

export function layoutGraph(
  graph: LayoutInput,
  { width, height, seed, initial, ticks = TICKS, alpha = 1 }: LayoutOptions,
): GraphLayout {
  const rand = seededRandom(seed);
  const cx = width / 2;
  const cy = height / 2;
  const known = new Set(graph.domains.map((d) => d.id));
  // One anchor per populated domain on a ring, plus one for unclassified documents.
  const docDomains = graph.documents.map((d) => d.domains.filter((x) => known.has(x)));
  const populated = ringOrder(
    [...new Set(docDomains.flat())].sort((a, b) => a - b),
    docDomains,
  );
  const hasLoose = docDomains.some((d) => d.length === 0);
  const slots = populated.length + (hasLoose ? 1 : 0);
  // Anchors sit on an ellipse scaled to the canvas, inset enough for the labels.
  // With a problem in the middle the ring is always used, even for one domain.
  const ringed = slots > 1 || graph.problem !== null;
  const rx = ringed ? width * RING.x : 0;
  const ry = ringed ? height * RING.y : 0;
  const anchor = new Map<number | "loose", { x: number; y: number }>();
  const slotAt = (k: number) => {
    const a = -Math.PI / 2 + (2 * Math.PI * k) / Math.max(1, slots);
    return { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) };
  };
  populated.forEach((d, k) => anchor.set(d, slotAt(k)));
  if (hasLoose) anchor.set("loose", slotAt(populated.length));

  // Each document's anchor: the mean of its domains' anchors.
  const anchors = docDomains.map((domains) => {
    const as = domains.length ? domains.map((d) => anchor.get(d)!) : [anchor.get("loose")!];
    return {
      x: as.reduce((s, p) => s + p.x, 0) / as.length,
      y: as.reduce((s, p) => s + p.y, 0) / as.length,
      first: as[0]!,
    };
  });
  if (graph.problem && ringed && anchors.length > 1) {
    // Every idea points at the problem in the middle: spread the documents
    // evenly round the ring in the order of their domains' direction, instead
    // of letting the means pile up in one sector or in the middle.
    const angleOf = (a: (typeof anchors)[number]) => {
      let ux = (a.x - cx) / rx;
      let uy = (a.y - cy) / ry;
      if (Math.hypot(ux, uy) < 1e-3) {
        // Opposite domains cancel out: use the first one's direction.
        ux = (a.first.x - cx) / rx;
        uy = (a.first.y - cy) / ry;
      }
      return Math.atan2(uy, ux);
    };
    const order = anchors
      .map((a, i) => ({ i, angle: angleOf(a), id: graph.documents[i]!.id }))
      .sort((p, q) => p.angle - q.angle || p.id - q.id);
    const start = order[0]!.angle;
    order.forEach(({ i }, k) => {
      const a = start + (2 * Math.PI * k) / order.length;
      anchors[i] = { ...anchors[i]!, x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) };
    });
  }

  const nodes: SimNode[] = graph.documents.map((doc, i) => {
    const domains = docDomains[i]!;
    const { x: ax, y: ay } = anchors[i]!;
    // Draw the jitter either way so the random stream stays aligned.
    const jx = (rand() - 0.5) * 40;
    const jy = (rand() - 0.5) * 40;
    const warm = initial?.get(doc.id);
    const r = nodeRadius(doc.idea_count);
    return {
      id: doc.id,
      r,
      domains,
      ax,
      ay,
      hw: Math.max(r, labelWidth(doc.title) / 2),
      up: r,
      down: r + LABEL_H,
      x: warm ? warm.x : ax + jx,
      y: warm ? warm.y : ay + jy,
    };
  });
  const problemNode: SimNode | null = graph.problem
    ? {
        id: -1,
        r: PROBLEM_HALF.w,
        domains: [],
        ax: cx,
        ay: cy,
        hw: PROBLEM_HALF.w + 4,
        up: PROBLEM_HALF.h + ROLE_ABOVE,
        down: PROBLEM_HALF.h + 4,
        x: cx,
        y: cy,
        fx: cx,
        fy: cy,
      }
    : null;
  const all = problemNode ? [...nodes, problemNode] : nodes;

  const reach = Math.max(width, height) / 2;
  const sim = forceSimulation<SimNode>(all)
    .randomSource(rand)
    .force("charge", forceManyBody<SimNode>().strength(-150).distanceMax(reach))
    .force("collide", boxCollideForce(0.5))
    .force("anchorX", forceX<SimNode>((n) => n.ax).strength(0.08))
    .force("anchorY", forceY<SimNode>((n) => n.ay).strength(0.08))
    .force("cluster", clusterForce(0.05))
    .force("contain", containForce(width, height, 0.3))
    .alpha(alpha)
    .stop();
  for (let i = 0; i < ticks; i++) sim.tick();
  // The forces leave a few near-misses; resolve them exactly.
  separateBoxes(all);

  const problem = problemNode ? { x: cx, y: cy } : null;
  const outNodes: LayoutNode[] = nodes.map((n) => ({
    id: n.id,
    x: round(n.x ?? 0),
    y: round(n.y ?? 0),
    r: round(n.r),
  }));
  return { nodes: outNodes, hulls: domainHulls(graph, outNodes, problem), problem };
}

// --- hull adjacency (for the client-side colour fallback) ---
// Deliberately NOT the server's adjacency. The server (ideation/domains.py,
// ruling O3) has no layout, so it calls domains adjacent when they share a
// document or a citation edge. The client sees the drawn hulls, and the brief's
// spatial rule (overlap or within 24 px) is the one the eye actually compares.
// It is used only for a domain that arrived without a server color_index.

/** The polygon's closed edges, as [from, to] pairs. */
function edges(poly: readonly Point[]): [Point, Point][] {
  return poly.map((p, i) => [p, poly[(i + 1) % poly.length] ?? p]);
}

function insidePolygon([x, y]: Point, poly: readonly Point[]): boolean {
  let inside = false;
  for (const [[xi, yi], [xj, yj]] of edges(poly)) {
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function pointSegmentDistance([px, py]: Point, [ax, ay]: Point, [bx, by]: Point): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function segmentsCross(a: Point, b: Point, c: Point, d: Point): boolean {
  const o = (p: Point, q: Point, r: Point) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  return o(a, b, c) * o(a, b, d) < 0 && o(c, d, a) * o(c, d, b) < 0;
}

/** Distance between two polygons; 0 when they intersect or one contains the other. */
export function polygonDistance(p: readonly Point[], q: readonly Point[]): number {
  if (p.some((pt) => insidePolygon(pt, q)) || q.some((pt) => insidePolygon(pt, p))) return 0;
  let best = Infinity;
  const qe = edges(q);
  for (const [a, b] of edges(p)) {
    for (const [c, d] of qe) {
      if (segmentsCross(a, b, c, d)) return 0;
      best = Math.min(
        best,
        pointSegmentDistance(a, c, d),
        pointSegmentDistance(b, c, d),
        pointSegmentDistance(c, a, b),
        pointSegmentDistance(d, a, b),
      );
    }
  }
  return best;
}

/** Domains whose hulls intersect or come within `gap` px of each other. */
export function hullAdjacency(hulls: readonly LayoutHull[], gap = ADJACENT_GAP): DomainAdjacency {
  const adj = new Map<number, Set<number>>();
  for (const h of hulls) adj.set(h.domainId, new Set());
  hulls.forEach((a, i) => {
    for (const b of hulls.slice(i + 1)) {
      if (polygonDistance(a.points, b.points) <= gap) {
        adj.get(a.domainId)?.add(b.domainId);
        adj.get(b.domainId)?.add(a.domainId);
      }
    }
  });
  return adj;
}

/** Width (px) of the ideas detail drawer, which overlays the graph's right edge. */
export const DRAWER_WIDTH = 340;
/** Half-width of a node's title label (24 characters at 11 px), for keep-visible maths. */
const LABEL_HALF_W = 72;
/** A node's label sits below it; its role tag ("Source") sits above. */
const LABEL_BELOW = 18;

/** Points around a node and its labels that must stay on screen. */
function nodeAnchors(n: LayoutNode): Point[] {
  return [
    [n.x - LABEL_HALF_W, n.y - n.r - ROLE_ABOVE],
    [n.x + LABEL_HALF_W, n.y + n.r + LABEL_BELOW],
  ];
}

function problemAnchors(p: { x: number; y: number }): Point[] {
  return [
    [p.x - PROBLEM_HALF.w, p.y - PROBLEM_HALF.h - ROLE_ABOVE],
    [p.x + PROBLEM_HALF.w, p.y + PROBLEM_HALF.h],
  ];
}

/**
 * Drawing points to keep clear of the drawer for a selection: both endpoints
 * of an idea with their labels, or a document with its label. Empty when the
 * selection has no position (unknown id, domain, layout not ready).
 */
export function selectionAnchors(
  layout: GraphLayout | null,
  ideas: readonly { id: number; source_doc: number; target_doc: number | null }[],
  sel: { kind: string; id?: number } | null,
): Point[] {
  if (!layout || !sel) return [];
  const node = (id: number) => layout.nodes.find((n) => n.id === id);
  if (sel.kind === "doc" && sel.id !== undefined) {
    const n = node(sel.id);
    return n ? nodeAnchors(n) : [];
  }
  if (sel.kind === "problem") return layout.problem ? problemAnchors(layout.problem) : [];
  if (sel.kind !== "idea") return [];
  const idea = ideas.find((i) => i.id === sel.id);
  if (!idea) return [];
  const a = node(idea.source_doc);
  const out: Point[] = a ? nodeAnchors(a) : [];
  if (idea.target_doc === null) {
    if (layout.problem) out.push(...problemAnchors(layout.problem));
  } else {
    const b = node(idea.target_doc);
    if (b) out.push(...nodeAnchors(b));
  }
  return out;
}

/** Bounding box of every node, its labels, the problem and the domain hulls with their labels, for "fit". */
export function layoutBounds(layout: GraphLayout): { x: number; y: number; width: number; height: number } | null {
  const pts: Point[] = layout.nodes.flatMap(nodeAnchors);
  if (layout.problem) pts.push(...problemAnchors(layout.problem));
  if (pts.length === 0) return null;
  for (const h of layout.hulls) {
    pts.push(...h.points);
    if (h.label) pts.push([h.label.box.x0, h.label.box.y0], [h.label.box.x1, h.label.box.y1]);
  }
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return { x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y };
}

/** Every drawn item (each document with its labels, and the problem) as a [corner, corner] pair. */
export function layoutItemBoxes(layout: GraphLayout): Point[] {
  const pts: Point[] = layout.nodes.flatMap(nodeAnchors);
  if (layout.problem) pts.push(...problemAnchors(layout.problem));
  return pts;
}

// --- width-aware text and document-label collisions ---

/** Whether a character is drawn about one em wide (CJK, kana, hangul, full-width forms). */
function isWide(ch: string): boolean {
  const c = ch.codePointAt(0) ?? 0;
  return (
    (c >= 0x1100 && c <= 0x115f) ||
    (c >= 0x2e80 && c <= 0xa4cf) ||
    (c >= 0xac00 && c <= 0xd7a3) ||
    (c >= 0xf900 && c <= 0xfaff) ||
    (c >= 0xfe30 && c <= 0xfe6f) ||
    (c >= 0xff00 && c <= 0xff60) ||
    (c >= 0xffe0 && c <= 0xffe6)
  );
}

/** Approximate rendered width (px) of `s` at `fontSize`: wide glyphs one em, the rest ~0.55 em. */
export function textWidth(s: string, fontSize: number): number {
  let w = 0;
  for (const ch of s) w += (isWide(ch) ? 1 : 0.55) * fontSize;
  return w;
}

/** `s` cut to fit `maxPx` at `fontSize`, with one ellipsis at the end only. */
export function truncateToWidth(s: string, maxPx: number, fontSize: number): string {
  if (textWidth(s, fontSize) <= maxPx) return s;
  const room = maxPx - textWidth("…", fontSize);
  let out = "";
  let w = 0;
  for (const ch of s) {
    w += textWidth(ch, fontSize);
    if (w > room) break;
    out += ch;
  }
  return `${out.trimEnd()}…`;
}

/** How a document's label is drawn: not at all, full, or abbreviated; below or above its node. */
export interface LabelPlacement {
  mode: "full" | "short" | "hidden";
  side: "below" | "above";
}

/** The abbreviated label's length in characters. */
export const SHORT_LABEL_CHARS = 10;

export interface LabelInput {
  id: number;
  x: number;
  y: number;
  r: number;
  title: string;
  /** Higher draws first and so wins a collision (selected, related, many ideas). */
  priority: number;
  /** Always drawn full, below its node, and never given up (the selection). */
  pinned?: boolean;
}

/**
 * Collision-aware label placement. Labels are drawn at `1/max(k, 1)` of their
 * size, so zooming in separates them. Greedy by priority: each label tries
 * full-below, full-above, short-below, short-above and takes the first spot
 * clear of every placed label and of every node's circle; otherwise it is
 * hidden (shown on hover or focus instead).
 */
export function placeLabels(nodes: readonly LabelInput[], k: number): Map<number, LabelPlacement> {
  const s = 1 / Math.max(k, 1);
  const out = new Map<number, LabelPlacement>();
  const placed: Box[] = [];
  const circles: { id: number; box: Box }[] = nodes.map((n) => ({
    id: n.id,
    box: { x0: n.x - n.r, y0: n.y - n.r, x1: n.x + n.r, y1: n.y + n.r },
  }));
  const boxFor = (n: LabelInput, mode: "full" | "short", side: "below" | "above"): Box => {
    const text = truncateLabel(n.title, mode === "full" ? LABEL_CHARS : SHORT_LABEL_CHARS);
    const hw = (text.length * LABEL_CHAR_W * s) / 2;
    return side === "below"
      ? { x0: n.x - hw, y0: n.y + n.r + 2, x1: n.x + hw, y1: n.y + n.r + 2 + (LABEL_H - 2) * s }
      : { x0: n.x - hw, y0: n.y - n.r - 2 - (LABEL_H - 2) * s, x1: n.x + hw, y1: n.y - n.r - 2 };
  };
  const free = (n: LabelInput, b: Box) =>
    !placed.some((p) => boxesOverlap(p, b, 2)) &&
    !circles.some((c) => c.id !== n.id && boxesOverlap(c.box, b, 1));
  const order = [...nodes].sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned) || b.priority - a.priority || a.id - b.id);
  for (const n of order) {
    if (n.pinned) {
      out.set(n.id, { mode: "full", side: "below" });
      placed.push(boxFor(n, "full", "below"));
      continue;
    }
    let found: LabelPlacement | null = null;
    let box: Box | null = null;
    for (const mode of ["full", "short"] as const) {
      for (const side of ["below", "above"] as const) {
        const b = boxFor(n, mode, side);
        if (free(n, b)) {
          found = { mode, side };
          box = b;
          break;
        }
      }
      if (found) break;
    }
    if (found && box) {
      out.set(n.id, found);
      placed.push(box);
    } else {
      out.set(n.id, { mode: "hidden", side: "below" });
    }
  }
  return out;
}
