import type { Element, ElementContent, Root, Text } from "hast";
import { visit } from "unist-util-visit";

import { NOTE_MARKER_RE, parseNoteIds } from "@/lib/figureCitations";

/**
 * Rehype plugin: rewrite `[note:b_…]` text into
 * `<note-cite data-block data-ordinal>` elements (one per cited block),
 * numbered in first-seen order within the message.
 */
export function rehypeNoteCitations() {
  return (tree: Root): void => {
    const ordinals = new Map<string, number>();
    visit(tree, "text", (node: Text, index, parent) => {
      if (parent == null || index == null) return;
      if (!node.value.includes("[note:")) return;
      const out: ElementContent[] = [];
      let last = 0;
      const re = new RegExp(NOTE_MARKER_RE.source, "g");
      let m: RegExpExecArray | null;
      while ((m = re.exec(node.value)) !== null) {
        if (m.index > last) out.push({ type: "text", value: node.value.slice(last, m.index) });
        for (const id of parseNoteIds(m[1] ?? "")) {
          if (!ordinals.has(id)) ordinals.set(id, ordinals.size + 1);
          out.push({
            type: "element",
            tagName: "note-cite",
            properties: { dataBlock: id, dataOrdinal: ordinals.get(id) ?? 0 },
            children: [],
          });
        }
        last = m.index + m[0].length;
      }
      if (last < node.value.length) out.push({ type: "text", value: node.value.slice(last) });
      (parent as Element).children.splice(index, 1, ...out);
      return index + out.length;
    });
  };
}
