import { buildTargets } from "@/lib/findAndHighlight";

export interface PdfPassageMatch {
  /** 1-based page number the passage was found on. */
  pageNumber: number;
  /** Indices (into that page's text items) overlapping the matched extent. */
  itemIndexes: Set<number>;
}

const normalize = (s: string): string => s.replace(/\s+/g, " ").trim();

/**
 * Locate a chunk passage within a PDF's text items.
 *
 * `pages[p]` is the ordered list of pdf.js text-item strings for page p
 * (pageNumber = p + 1). The START of the passage is found with the same
 * resolver as the HTML view (`buildTargets`: longest normalized prefix →
 * shorter fallbacks), so a passage whose tail was mangled (math, ligatures)
 * still lands. The END extends to the FULL normalized chunk length (capped at
 * the page text) so the whole chunk highlights, not just the matched prefix.
 *
 * Returns the first page containing a target, plus every item index overlapping
 * `[start, start + fullLength)`, or null if nothing matched.
 */
export function locatePassage(
  pages: string[][],
  needle: string,
): PdfPassageMatch | null {
  const targets = buildTargets(needle);
  if (targets.length === 0) return null;
  const fullLength = normalize(needle).length;

  for (let p = 0; p < pages.length; p++) {
    const items = pages[p];
    if (!items) continue;
    const ranges: { idx: number; start: number; end: number }[] = [];
    let combined = "";
    items.forEach((raw, idx) => {
      const n = normalize(raw);
      if (!n) return;
      const prefix = combined.length > 0 ? " " : "";
      const start = combined.length + prefix.length;
      combined += prefix + n;
      ranges.push({ idx, start, end: combined.length });
    });

    for (const target of targets) {
      const hit = combined.indexOf(target);
      if (hit < 0) continue;
      const matchEnd = Math.min(hit + fullLength, combined.length);
      const itemIndexes = new Set<number>();
      for (const r of ranges) {
        // overlaps [hit, matchEnd)
        if (r.start < matchEnd && r.end > hit) itemIndexes.add(r.idx);
      }
      return { pageNumber: p + 1, itemIndexes };
    }
  }
  return null;
}
