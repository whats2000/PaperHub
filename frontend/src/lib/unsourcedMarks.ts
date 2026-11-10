import type { Element, ElementContent, Root, Text } from "hast";
import { visit } from "unist-util-visit";

/** `[unsourced:<reason>]` — put by the backend's citation validator where a
 * citation failed (a passage not read, a figure or region not viewed, a quote
 * not in its source), so the reader sees the claim is unverified instead of a
 * citation silently disappearing. Capture group 1 is the reason. */
export const UNSOURCED_RE = /\[unsourced:\s*([a-z_]+)\s*\]/g;

/** Reasons the backend writes; anything else shows the generic text. */
export const UNSOURCED_REASONS = [
  "not_read",
  "figure_not_viewed",
  "region_not_viewed",
  "quote_not_found",
] as const;

/** Rehype plugin: `[unsourced:<reason>]` → `<unsourced-mark data-reason>`. */
export function rehypeUnsourcedMarks() {
  return (tree: Root): void => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (parent == null || index == null) return;
      if (!node.value.includes("[unsourced:")) return;
      const out: ElementContent[] = [];
      let last = 0;
      const re = new RegExp(UNSOURCED_RE.source, "g");
      let m: RegExpExecArray | null;
      while ((m = re.exec(node.value)) !== null) {
        if (m.index > last) out.push({ type: "text", value: node.value.slice(last, m.index) });
        out.push({
          type: "element",
          tagName: "unsourced-mark",
          properties: { dataReason: m[1] ?? "" },
          children: [],
        });
        last = m.index + m[0].length;
      }
      if (last < node.value.length) out.push({ type: "text", value: node.value.slice(last) });
      (parent as Element).children.splice(index, 1, ...out);
      return index + out.length;
    });
  };
}
