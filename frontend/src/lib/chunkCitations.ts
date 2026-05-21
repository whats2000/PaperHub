/** Matches `[chunk:<id>]` markers — and multi-id markers like `[chunk:101,102]`
 * or `[chunk:75143, 75161]` (the paper_qa finalizer may cite several chunks in
 * one marker, sometimes with spaces). `g` flag drives matchAll / exec; capture
 * group 1 is the raw id list (e.g. "75143, 75161"). */
export const CHUNK_MARKER_RE = /\[chunk:\s*(\d+(?:\s*,\s*\d+)*)\s*\]/g;

/** Parse a marker's id-list capture (e.g. "75143, 75161") into chunk ids. */
export function parseChunkIds(idList: string): number[] {
  return idList
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n));
}

/**
 * Map each distinct chunk id in a message to its citation ordinal (1-based),
 * assigned in order of first appearance and deduped (a re-cited chunk keeps
 * its first ordinal). Multi-id markers contribute each id in order. Used to
 * render academic-style superscripts.
 */
export function buildChunkOrdinalMap(content: string): Map<number, number> {
  const map = new Map<number, number>();
  for (const match of content.matchAll(CHUNK_MARKER_RE)) {
    for (const id of parseChunkIds(match[1] ?? "")) {
      if (!map.has(id)) map.set(id, map.size + 1);
    }
  }
  return map;
}
