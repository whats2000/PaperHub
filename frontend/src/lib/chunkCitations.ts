/** Matches `[chunk:<id>]` markers emitted by the paper_qa finalizer.
 * `g` flag so it can drive matchAll / replace; capture group 1 is the id. */
export const CHUNK_MARKER_RE = /\[chunk:(\d+)\]/g;

/**
 * Map each distinct chunk id in a message to its citation ordinal (1-based),
 * assigned in order of first appearance and deduped (a re-cited chunk keeps
 * its first ordinal). Used to render academic-style superscripts.
 */
export function buildChunkOrdinalMap(content: string): Map<number, number> {
  const map = new Map<number, number>();
  for (const match of content.matchAll(CHUNK_MARKER_RE)) {
    const id = Number(match[1]);
    if (!map.has(id)) map.set(id, map.size + 1);
  }
  return map;
}
