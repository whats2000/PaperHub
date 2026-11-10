import type { Element, ElementContent, Root, Text } from "hast";
import { visit } from "unist-util-visit";

import {
  EVIDENCE_MARKER_RE,
  buildFigureOrdinalMap,
  parseFigureKeys,
  parseViewKey,
} from "@/lib/figureCitations";

/**
 * Rehype plugin: rewrite `[fig:<paper>/<figure id>]` text into
 * `<fig-cite data-paper data-figure data-ordinal>` elements (one per cited
 * figure) and `[view:<paper>/<page>/<box>]` into
 * `<view-cite data-paper data-page data-box data-ordinal>`. Figures and page
 * regions share one numbering, in first-seen order.
 */
export function rehypeFigureCitations() {
  return (tree: Root): void => {
    let full = "";
    visit(tree, "text", (node: Text) => {
      full += node.value;
    });
    const ordinals = buildFigureOrdinalMap(full);
    if (ordinals.size === 0) return;

    visit(tree, "text", (node: Text, index, parent) => {
      if (parent == null || index == null) return;
      if (!node.value.includes("[fig:") && !node.value.includes("[view:")) return;

      const out: ElementContent[] = [];
      let last = 0;
      const re = new RegExp(EVIDENCE_MARKER_RE.source, "g");
      let m: RegExpExecArray | null;
      while ((m = re.exec(node.value)) !== null) {
        if (m.index > last) {
          out.push({ type: "text", value: node.value.slice(last, m.index) });
        }
        if (m[1] !== undefined) {
          for (const ref of parseFigureKeys(m[1])) {
            out.push({
              type: "element",
              tagName: "fig-cite",
              properties: {
                dataPaper: ref.paper,
                dataFigure: ref.figureId,
                dataOrdinal: ordinals.get(ref.key) ?? 0,
              },
              children: [],
            });
          }
        } else if (m[2] !== undefined) {
          const ref = parseViewKey(m[2]);
          out.push({
            type: "element",
            tagName: "view-cite",
            properties: {
              dataPaper: ref.paper,
              dataPage: ref.page,
              dataBox: ref.box.join(","),
              dataOrdinal: ordinals.get(ref.key) ?? 0,
            },
            children: [],
          });
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
