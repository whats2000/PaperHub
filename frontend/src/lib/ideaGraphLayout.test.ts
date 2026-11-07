import { describe, expect, it } from "vitest";

import type { GraphResponse } from "@/types/ideation";

import exploreLibrary from "./__fixtures__/exploreLibrary.json";

import {
  boxesOverlap,
  placeLabels,
  textWidth,
  truncateToWidth,
  hullAdjacency,
  labelBox,
  layoutBounds,
  layoutGraph,
  NODE_GAP,
  nodeBox,
  nodeRadius,
  selectionAnchors,
  WARM,
  type GraphLayout,
} from "./ideaGraphLayout";

function graph(): Pick<GraphResponse, "documents" | "domains" | "problem"> {
  const documents = [
    ...Array.from({ length: 6 }, (_, i) => ({ id: 10 + i, title: `A${i}`, domains: [1], idea_count: i % 3, is_private: false })),
    ...Array.from({ length: 6 }, (_, i) => ({ id: 20 + i, title: `B${i}`, domains: [2], idea_count: 0, is_private: false })),
    { id: 99, title: "AB", domains: [1, 2], idea_count: 4, is_private: true },
  ];
  return {
    documents,
    domains: [
      { id: 1, name: "Vision", color_index: 0 },
      { id: 2, name: "Robotics", color_index: 2 },
    ],
    problem: null,
  };
}

const OPTS = { width: 1000, height: 700, seed: 7 };

function centroid(nodes: { x: number; y: number }[]) {
  return {
    x: nodes.reduce((s, n) => s + n.x, 0) / nodes.length,
    y: nodes.reduce((s, n) => s + n.y, 0) / nodes.length,
  };
}

describe("layoutGraph", () => {
  it("is identical for the same seed", () => {
    const a = layoutGraph(graph(), OPTS);
    const b = layoutGraph(graph(), OPTS);
    expect(b).toEqual(a);
    expect(a.nodes).toHaveLength(13);
  });

  it("differs for another seed", () => {
    const a = layoutGraph(graph(), OPTS);
    const b = layoutGraph(graph(), { ...OPTS, seed: 8 });
    expect(b.nodes).not.toEqual(a.nodes);
  });

  it("settles a multi-domain node between its two cluster centroids", () => {
    const { nodes } = layoutGraph(graph(), OPTS);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const ca = centroid(nodes.filter((n) => n.id >= 10 && n.id < 20));
    const cb = centroid(nodes.filter((n) => n.id >= 20 && n.id < 30));
    const m = byId.get(99)!;
    // Project the node onto the centroid segment: strictly inside, and close to it.
    const dx = cb.x - ca.x;
    const dy = cb.y - ca.y;
    const len2 = dx * dx + dy * dy;
    const t = ((m.x - ca.x) * dx + (m.y - ca.y) * dy) / len2;
    expect(t).toBeGreaterThan(0.2);
    expect(t).toBeLessThan(0.8);
    const off = Math.abs((m.x - ca.x) * dy - (m.y - ca.y) * dx) / Math.sqrt(len2);
    expect(off).toBeLessThan(Math.sqrt(len2) / 2);
  });

  it("sizes a node by its idea count", () => {
    const { nodes } = layoutGraph(graph(), OPTS);
    expect(nodes.find((n) => n.id === 99)!.r).toBeCloseTo(nodeRadius(4));
    expect(nodeRadius(4)).toBe(14);
    expect(nodeRadius(0)).toBe(8);
  });

  it("pads each domain hull around its nodes", () => {
    const { nodes, hulls } = layoutGraph(graph(), OPTS);
    expect(hulls.map((h) => h.domainId).sort()).toEqual([1, 2]);
    const hull = hulls.find((h) => h.domainId === 1)!;
    expect(hull.points.length).toBeGreaterThanOrEqual(3);
    // Every member lies inside the hull's bounding box, with the padding.
    const xs = hull.points.map((p) => p[0]);
    for (const n of nodes.filter((n) => n.id < 20 || n.id === 99)) {
      expect(n.x - n.r - 10).toBeGreaterThanOrEqual(Math.min(...xs) - 0.5);
      expect(n.x + n.r + 10).toBeLessThanOrEqual(Math.max(...xs) + 0.5);
    }
  });

  it("places the problem node when the graph has a problem", () => {
    const g = { ...graph(), problem: { id: 1, text: "x", tried: "", created_at: "", idea_count: 0, status: "ready" as const } };
    const out = layoutGraph(g, OPTS);
    expect(out.problem).not.toBeNull();
    expect(layoutGraph(graph(), OPTS).problem).toBeNull();
  });

  it("warm-starts from previous positions, so a count change barely moves nodes", () => {
    const cold = layoutGraph(graph(), OPTS);
    const initial = new Map(cold.nodes.map((n) => [n.id, n]));
    const g = graph();
    g.documents = g.documents.map((d) => (d.id === 12 ? { ...d, idea_count: 6 } : d));
    const warm = layoutGraph(g, { ...OPTS, initial, ...WARM });
    expect(warm).toEqual(layoutGraph(g, { ...OPTS, initial, ...WARM })); // still deterministic
    for (const n of warm.nodes) {
      const p = initial.get(n.id)!;
      expect(Math.hypot(n.x - p.x, n.y - p.y)).toBeLessThan(30);
    }
    expect(warm.nodes.find((n) => n.id === 12)!.r).toBeCloseTo(nodeRadius(6));
  });

  it("copes with a 60-document library", () => {
    const documents = Array.from({ length: 60 }, (_, i) => ({
      id: i + 1,
      title: `D${i}`,
      domains: i % 7 === 0 ? [1 + (i % 8), 1 + ((i + 1) % 8)] : [1 + (i % 8)],
      idea_count: i % 4,
      is_private: false,
    }));
    const domains = Array.from({ length: 8 }, (_, i) => ({ id: i + 1, name: `d${i}`, color_index: i }));
    const t0 = performance.now();
    const out = layoutGraph({ documents, domains, problem: null }, OPTS);
    expect(performance.now() - t0).toBeLessThan(1000);
    expect(out.nodes).toHaveLength(60);
    expect(out.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y))).toBe(true);
  });
});

/** A real problem scope: 8 documents across 8 overlapping domains, every idea aimed at the problem. */
function problemScope(): Pick<GraphResponse, "documents" | "domains" | "problem"> {
  const doc = (id: number, title: string, domains: number[], is_private = false) => ({
    id,
    title,
    domains,
    idea_count: 1,
    is_private,
  });
  return {
    documents: [
      doc(31, "Discrete Flow Matching", [3, 5]),
      doc(32, "One Step Diffusion via Shortcut Models", [2, 7]),
      doc(36, "RenAIssance: A Survey into AI Text-to-Image Generation in the Era of Large Model", [2, 3, 5]),
      doc(62, "FASTer: Toward Efficient Autoregressive Vision Language Action Modeling", [1, 2, 3]),
      doc(63, "Global Prior Meets Local Consistency: Dual-Memory Augmented VLA Model", [1, 10]),
      doc(79, "EfficientVLA: Training-Free Acceleration and Compression for VLA Models", [1, 2, 3]),
      doc(87, "Mamba: Linear-Time Sequence Modeling with Selective State Spaces", [3, 28, 29]),
      doc(91, "Sparse Routing for Efficient Sequence Models", [3, 5], true),
    ],
    domains: [
      { id: 1, name: "Robotics", color_index: 0 },
      { id: 2, name: "Computer Vision", color_index: 1 },
      { id: 3, name: "Natural Language Processing", color_index: 2 },
      { id: 5, name: "Machine Learning", color_index: 3 },
      { id: 7, name: "Generative Models", color_index: null },
      { id: 10, name: "Reinforcement Learning", color_index: 5 },
      { id: 28, name: "Genomics", color_index: null },
      { id: 29, name: "Audio", color_index: null },
    ],
    problem: { id: 1, text: "Fast action generation", tried: "", created_at: "", idea_count: 8, status: "ready" },
  };
}

/** A 60-document library: most documents in one domain, every seventh in two. */
function library(): Pick<GraphResponse, "documents" | "domains" | "problem"> {
  const documents = Array.from({ length: 60 }, (_, i) => ({
    id: i + 1,
    title: `A fairly long paper title number ${i}`,
    domains: i % 7 === 0 ? [1 + (i % 8), 1 + ((i + 1) % 8)] : [1 + (i % 8)],
    idea_count: i % 4,
    is_private: false,
  }));
  const domains = Array.from({ length: 8 }, (_, i) => ({ id: i + 1, name: `Domain number ${i}`, color_index: i }));
  return { documents, domains, problem: null };
}

function pairs<T>(xs: readonly T[]): [T, T][] {
  return xs.flatMap((a, i) => xs.slice(i + 1).map((b) => [a, b] as [T, T]));
}

describe("layoutGraph readability", () => {
  const SIZE = { width: 900, height: 594, seed: 1 };
  const titles = (g: Pick<GraphResponse, "documents">) => new Map(g.documents.map((d) => [d.id, d.title]));

  it("never lets two documents overlap", () => {
    for (const g of [problemScope(), library()]) {
      const { nodes } = layoutGraph(g, SIZE);
      for (const [a, b] of pairs(nodes)) {
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(a.r + b.r + NODE_GAP);
      }
    }
  });

  it("keeps every document clear of the problem node", () => {
    const g = problemScope();
    const t = titles(g);
    const out = layoutGraph(g, SIZE);
    const p = out.problem!;
    const problem = { x0: p.x - 56, y0: p.y - 18, x1: p.x + 56, y1: p.y + 18 };
    for (const n of out.nodes) expect(boxesOverlap(nodeBox(n, t.get(n.id)!), problem)).toBe(false);
  });

  it("spreads the problem scope over the canvas instead of a clump in the middle", () => {
    const { nodes } = layoutGraph(problemScope(), SIZE);
    const xs = nodes.flatMap((n) => [n.x - n.r, n.x + n.r]);
    const ys = nodes.flatMap((n) => [n.y - n.r, n.y + n.r]);
    const w = (Math.max(...xs) - Math.min(...xs)) / SIZE.width;
    const h = (Math.max(...ys) - Math.min(...ys)) / SIZE.height;
    expect(w).toBeGreaterThan(0.5);
    expect(h).toBeGreaterThan(0.5);
    // ...and not far past it either.
    expect(w).toBeLessThan(1.2);
    expect(h).toBeLessThan(1.2);
  });

  it("rings a single-domain problem scope round the problem too", () => {
    const g = problemScope();
    g.documents = g.documents.map((d) => ({ ...d, domains: [1] }));
    const out = layoutGraph(g, SIZE);
    const p = out.problem!;
    // Spread on every side of the problem, not pushed into one corner.
    expect(out.nodes.some((n) => n.x < p.x - 150)).toBe(true);
    expect(out.nodes.some((n) => n.x > p.x + 150)).toBe(true);
    expect(out.nodes.some((n) => n.y < p.y - 100)).toBe(true);
    expect(out.nodes.some((n) => n.y > p.y + 100)).toBe(true);
  });

  it("never overlaps two document labels", () => {
    for (const g of [problemScope(), library()]) {
      const t = titles(g);
      const { nodes } = layoutGraph(g, SIZE);
      for (const [a, b] of pairs(nodes)) {
        expect(boxesOverlap(labelBox(a, t.get(a.id)!), labelBox(b, t.get(b.id)!))).toBe(false);
      }
    }
  });

  it("puts each domain label by its own hull, never on another domain label", () => {
    for (const g of [problemScope(), library()]) {
      const out: GraphLayout = layoutGraph(g, SIZE);
      expect(out.hulls.every((h) => h.label !== undefined)).toBe(true);
      for (const [a, b] of pairs(out.hulls)) expect(boxesOverlap(a.label!.box, b.label!.box)).toBe(false);
      for (const h of out.hulls) {
        // Within a few label rows of a point on the hull's outline.
        const { box } = h.label!;
        const cy = (box.y0 + box.y1) / 2;
        const outline = h.points.flatMap(([x, y], i) => {
          const [qx, qy] = h.points[(i + 1) % h.points.length]!;
          return Array.from({ length: 5 }, (_, k) => [x + ((qx - x) * k) / 4, y + ((qy - y) * k) / 4]);
        });
        const near = outline.some(([x, y]) => x! >= box.x0 - 1 && x! <= box.x1 + 1 && Math.abs(y! - cy) < 110);
        expect(near).toBe(true);
      }
    }
  });

  it("keeps the problem scope's domain labels off the documents and their labels", () => {
    const g = problemScope();
    const t = titles(g);
    const out = layoutGraph(g, SIZE);
    for (const h of out.hulls) {
      for (const n of out.nodes) {
        const circle = { x0: n.x - n.r, y0: n.y - n.r, x1: n.x + n.r, y1: n.y + n.r };
        expect(boxesOverlap(h.label!.box, circle)).toBe(false);
        expect(boxesOverlap(h.label!.box, labelBox(n, t.get(n.id)!))).toBe(false);
      }
    }
  });

  it("fits the domain labels into the layout bounds", () => {
    const out = layoutGraph(problemScope(), SIZE);
    const b = layoutBounds(out)!;
    for (const h of out.hulls) {
      expect(h.label!.box.x0).toBeGreaterThanOrEqual(b.x - 0.01);
      expect(h.label!.box.y0).toBeGreaterThanOrEqual(b.y - 0.01);
      expect(h.label!.box.x1).toBeLessThanOrEqual(b.x + b.width + 0.01);
      expect(h.label!.box.y1).toBeLessThanOrEqual(b.y + b.height + 0.01);
    }
  });
});

describe("hullAdjacency", () => {
  const square = (x: number, y: number, s = 100): [number, number][] => [
    [x, y],
    [x + s, y],
    [x + s, y + s],
    [x, y + s],
  ];

  it("links overlapping hulls and hulls within 24 px, not distant ones", () => {
    const adj = hullAdjacency([
      { domainId: 1, points: square(0, 0) },
      { domainId: 2, points: square(50, 50) }, // overlaps 1
      { domainId: 3, points: square(120, 0) }, // 20 px from 1
      { domainId: 4, points: square(500, 500) },
    ]);
    expect([...(adj.get(1) ?? [])].sort()).toEqual([2, 3]);
    expect(adj.get(2)?.has(1)).toBe(true);
    expect(adj.get(4)?.size ?? 0).toBe(0);
  });

  it("links a hull nested inside another", () => {
    const adj = hullAdjacency([
      { domainId: 1, points: square(0, 0, 300) },
      { domainId: 2, points: square(100, 100, 50) },
    ]);
    expect(adj.get(2)?.has(1)).toBe(true);
  });
});

describe("selectionAnchors / layoutBounds", () => {
  const layout = {
    nodes: [
      { id: 1, x: 100, y: 100, r: 10 },
      { id: 2, x: 500, y: 300, r: 8 },
    ],
    hulls: [],
    problem: { x: 300, y: 200 },
  };
  const ideas = [
    { id: 11, source_doc: 1, target_doc: 2 },
    { id: 12, source_doc: 2, target_doc: null },
  ];

  it("covers both endpoints of an idea and their labels", () => {
    const pts = selectionAnchors(layout, ideas, { kind: "idea", id: 11 });
    const xs = pts.map((p) => p[0]);
    expect(Math.min(...xs)).toBeLessThan(100);
    expect(Math.max(...xs)).toBeGreaterThan(500);
  });

  it("uses the problem as a problem-mode idea's target", () => {
    const pts = selectionAnchors(layout, ideas, { kind: "idea", id: 12 });
    expect(pts.some(([x]) => x < 300 && x > 200)).toBe(true);
  });

  it("is empty for unknown ids, domains and no layout", () => {
    expect(selectionAnchors(layout, ideas, { kind: "idea", id: 99 })).toEqual([]);
    expect(selectionAnchors(layout, ideas, { kind: "domain", id: 1 })).toEqual([]);
    expect(selectionAnchors(null, ideas, { kind: "doc", id: 1 })).toEqual([]);
  });

  it("bounds every node with its labels", () => {
    const b = layoutBounds(layout)!;
    expect(b.x).toBeLessThan(100);
    expect(b.x + b.width).toBeGreaterThan(500);
    expect(layoutBounds({ nodes: [], hulls: [], problem: null })).toBeNull();
  });
});

describe("layoutGraph on the live explore scope", () => {
  // The live 61-document free-exploration library (29 domains), titles replaced by same-length placeholders.
  const g = exploreLibrary as Pick<GraphResponse, "documents" | "domains" | "problem">;
  const SIZE = { width: 1172, height: 774, seed: 1 };
  const t = new Map(g.documents.map((d) => [d.id, d.title]));
  const circle = (n: { x: number; y: number; r: number }) => ({ x0: n.x - n.r, y0: n.y - n.r, x1: n.x + n.r, y1: n.y + n.r });

  it("keeps every domain label off the other domain labels, the documents and their labels", () => {
    const out = layoutGraph(g, SIZE);
    expect(out.hulls.every((h) => h.label !== undefined)).toBe(true);
    for (const [a, b] of pairs(out.hulls)) expect(boxesOverlap(a.label!.box, b.label!.box, 10)).toBe(false);
    for (const h of out.hulls) {
      for (const n of out.nodes) {
        expect(boxesOverlap(h.label!.box, circle(n))).toBe(false);
        expect(boxesOverlap(h.label!.box, labelBox(n, t.get(n.id)!))).toBe(false);
      }
    }
  });
});

describe("layoutGraph uses its canvas", () => {
  it("keeps the explore scope within its canvas, so the fit isn't shrunk by stragglers", () => {
    const g = exploreLibrary as Pick<GraphResponse, "documents" | "domains" | "problem">;
    const SIZE = { width: 1172, height: 774, seed: 1 };
    const b = layoutBounds(layoutGraph(g, SIZE))!;
    expect(b.width).toBeLessThanOrEqual(SIZE.width * 1.05);
    expect(b.height).toBeLessThanOrEqual(SIZE.height * 1.05);
  });
});

describe("width-aware text and label placement", () => {
  it("cuts CJK text by width with a single trailing ellipsis", () => {
    const text = "我們的機器人操作策略在推論時需要許多昂貴的去噪步驟";
    const cut = truncateToWidth(text, 100, 9.5);
    expect(textWidth(cut, 9.5)).toBeLessThanOrEqual(100);
    expect(cut.endsWith("…")).toBe(true);
    expect(cut.indexOf("…")).toBe(cut.length - 1);
    expect(text.startsWith(cut.slice(0, -1))).toBe(true);
  });

  it("leaves text that already fits alone", () => {
    expect(truncateToWidth("Short text", 100, 9.5)).toBe("Short text");
  });

  it("moves a colliding label above, hides one with no room, and keeps the pinned one", () => {
    const nodes = [
      { id: 1, x: 100, y: 100, r: 8, title: "A very long document title here", priority: 5 },
      { id: 2, x: 104, y: 100, r: 8, title: "Another long document title here", priority: 1 },
      { id: 3, x: 102, y: 100, r: 8, title: "Pinned document with a long title", priority: 0, pinned: true },
    ];
    const out = placeLabels(nodes, 1);
    expect(out.get(3)).toEqual({ mode: "full", side: "below" });
    expect(out.get(1)).toEqual({ mode: "full", side: "above" });
    expect(out.get(2)?.mode).toBe("hidden");
  });

  it("shows more labels once zoomed in far enough that they separate", () => {
    const nodes = [1, 2, 3].map((i) => ({ id: i, x: 60 + 40 * i, y: 100, r: 8, title: "Document one title", priority: 5 - i }));
    expect(placeLabels(nodes, 1).get(3)?.mode).toBe("hidden");
    expect(placeLabels(nodes, 3).get(3)?.mode).toBe("full");
  });
});
