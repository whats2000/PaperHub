import { describe, expect, it } from "vitest";

import { assignDomainColors, domainColorVar, pairPasses, PASSING_PAIRS, type DomainAdjacency } from "./domainColors";
import palette from "./domainPalette.json";

function adjacency(pairs: [number, number][]): DomainAdjacency {
  const m = new Map<number, Set<number>>();
  for (const [a, b] of pairs) {
    if (!m.has(a)) m.set(a, new Set());
    if (!m.has(b)) m.set(b, new Set());
    m.get(a)!.add(b);
    m.get(b)!.add(a);
  }
  return m;
}

describe("PASSING_PAIRS", () => {
  it("is loaded from the validated palette file", () => {
    expect(PASSING_PAIRS.size).toBe(palette.passing_pairs.length);
    expect(pairPasses(0, 1)).toBe(true);
    expect(pairPasses(1, 0)).toBe(true);
    // 0-6 (blue / violet) fails the validator.
    expect(pairPasses(0, 6)).toBe(false);
  });
});

describe("assignDomainColors", () => {
  it("keeps every existing slot when a 9th domain arrives, and the 9th is Other", () => {
    const existing = [3, 0, 5, 1, 7, 2, 6, 4].map((c, i) => ({ id: i + 1, color_index: c }));
    const out = assignDomainColors([...existing, { id: 9, color_index: null }], adjacency([]));
    for (const d of existing) expect(out.get(d.id)).toBe(d.color_index);
    expect(out.get(9)).toBeNull();
  });

  it("never gives adjacent domains a non-passing pair", () => {
    // A dense neighbourhood: every domain touches its two predecessors.
    const domains = Array.from({ length: 8 }, (_, i) => ({ id: i + 1 }));
    const pairs: [number, number][] = [];
    for (let i = 1; i <= 8; i++) {
      if (i > 1) pairs.push([i, i - 1]);
      if (i > 2) pairs.push([i, i - 2]);
    }
    const adj = adjacency(pairs);
    const out = assignDomainColors(domains, adj);
    for (const [a, b] of pairs) {
      const ca = out.get(a);
      const cb = out.get(b);
      if (ca != null && cb != null) expect(pairPasses(ca, cb)).toBe(true);
    }
    const used = [...out.values()].filter((c) => c !== null);
    expect(new Set(used).size).toBe(used.length);
  });

  it("takes the lowest passing slot, skipping one that fails with a neighbour", () => {
    // Domain 1 holds slot 6 (violet); slot 0 (blue) fails with it, so domain 2 takes 1.
    const out = assignDomainColors([{ id: 1, color_index: 6 }, { id: 2 }], adjacency([[1, 2]]));
    expect(out.get(2)).toBe(1);
  });

  it("maps a slot to its CSS token and null to the neutral gray", () => {
    expect(domainColorVar(0)).toBe("var(--domain-1)");
    expect(domainColorVar(null)).toBe("var(--muted-foreground)");
  });
});
