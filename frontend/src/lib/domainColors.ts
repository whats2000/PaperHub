// Domain colours for the ideas graph (Plan H3).
//
// The server assigns `color_index` (backend/src/paperhub/ideation/domains.py)
// with the same palette and pair table; this mirror is the client-side fallback
// for a domain that arrives without one, and lets tests pin the rule.
import palette from "./domainPalette.json";

/** Number of coloured palette slots; any further domain is gray "Other". */
export const PALETTE_SIZE = 8;

function pairKey(a: number, b: number): string {
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}

/**
 * Slot pairs that pass every hard gate of the dataviz validator in both themes
 * (light vs the #ffffff card, dark vs the #171717 card).
 */
export const PASSING_PAIRS: ReadonlySet<string> = new Set(
  palette.passing_pairs.map(([a = -1, b = -1]) => pairKey(a, b)),
);

export function pairPasses(a: number, b: number): boolean {
  return PASSING_PAIRS.has(pairKey(a, b));
}

/** CSS colour for a palette slot; `null` is the neutral gray "Other". */
export function domainColorVar(index: number | null): string {
  return index === null ? "var(--muted-foreground)" : `var(--domain-${index + 1})`;
}

export interface ColorableDomain {
  id: number;
  /** An assigned slot, or null/undefined when the domain has none yet. */
  color_index?: number | null;
}

/** Symmetric adjacency: domain id → ids of the domains next to it. */
export type DomainAdjacency = ReadonlyMap<number, ReadonlySet<number>>;

/**
 * Give every domain a palette slot. An existing slot is never repainted (colour
 * follows the domain, not its rank). Uncoloured domains, in id order, take the
 * lowest unused slot whose pair with every coloured adjacent domain passes; with
 * more than 8 domains or no passing slot a domain stays `null` (gray "Other").
 */
export function assignDomainColors(
  domains: readonly ColorableDomain[],
  adjacency: DomainAdjacency,
): Map<number, number | null> {
  const color = new Map<number, number>();
  for (const d of domains) {
    if (d.color_index != null) color.set(d.id, d.color_index);
  }
  const pending = domains
    .filter((d) => d.color_index == null)
    .map((d) => d.id)
    .sort((a, b) => a - b);
  const out = new Map<number, number | null>();
  for (const d of domains) out.set(d.id, d.color_index ?? null);
  for (const id of pending) {
    const used = new Set(color.values());
    const neighbours: number[] = [];
    for (const n of adjacency.get(id) ?? []) {
      const c = color.get(n);
      if (c !== undefined) neighbours.push(c);
    }
    let slot: number | null = null;
    for (let i = 0; i < PALETTE_SIZE; i++) {
      if (!used.has(i) && neighbours.every((c) => pairPasses(i, c))) {
        slot = i;
        break;
      }
    }
    if (slot !== null) color.set(id, slot);
    out.set(id, slot);
  }
  return out;
}
