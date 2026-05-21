import type { Root, Text, Element, ElementContent } from "hast";
import { visit } from "unist-util-visit";
import {
  CHUNK_MARKER_RE,
  buildChunkOrdinalMap,
  parseChunkIds,
} from "@/lib/chunkCitations";

/**
 * Rehype plugin: rewrite `[chunk:<id>]` text occurrences into
 * `<chunk-cite data-chunk-id data-ordinal>` element nodes. The ordinal map is
 * built once per tree from the concatenated text so numbering + dedup are
 * stable across text nodes split by inline markdown.
 */
export function rehypeChunkCitations() {
  return (tree: Root): void => {
    let full = "";
    visit(tree, "text", (node: Text) => {
      full += node.value;
    });
    const ordinals = buildChunkOrdinalMap(full);
    if (ordinals.size === 0) return;

    visit(tree, "text", (node: Text, index, parent) => {
      if (parent == null || index == null) return;
      if (!node.value.includes("[chunk:")) return;

      const out: ElementContent[] = [];
      let last = 0;
      CHUNK_MARKER_RE.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = CHUNK_MARKER_RE.exec(node.value)) !== null) {
        if (m.index > last) {
          out.push({ type: "text", value: node.value.slice(last, m.index) });
        }
        // A marker may carry several ids (`[chunk:101,102]`); emit one
        // superscript per id (CitationMarker spaces adjacent ones).
        for (const id of parseChunkIds(m[1] ?? "")) {
          const ordinal = ordinals.get(id) ?? 0;
          const cite: Element = {
            type: "element",
            tagName: "chunk-cite",
            properties: { dataChunkId: id, dataOrdinal: ordinal },
            children: [],
          };
          out.push(cite);
        }
        last = m.index + m[0].length;
      }
      if (last < node.value.length) {
        out.push({ type: "text", value: node.value.slice(last) });
      }
      (parent as Element).children.splice(index, 1, ...out);
      return index + out.length;
    });
  };
}
