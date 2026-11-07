import type { Element, ElementContent, Root } from "hast";
import { visit } from "unist-util-visit";

// Models write `<br>` / `<br/>` / `<br />` to break a line inside a table
// cell (Markdown tables can't hold a newline). react-markdown never parses raw
// HTML: the tag reaches the tree as a `raw` node (shown as literal text
// later). Only this exact tag, inside a table cell, becomes a real `<br>`
// element; every other `raw` node is left alone to render as text.
const BR_ONLY = /^<br\s*\/?>$/i;
const CELLS = new Set(["td", "th"]);

interface RawLike {
  type: string;
  value?: string;
}

/** Rehype plugin: a literal `<br>` in a table cell becomes a line break. */
export function rehypeTableBreaks() {
  return (tree: Root): void => {
    visit(tree, "element", (cell: Element) => {
      if (!CELLS.has(cell.tagName)) return;
      cell.children = cell.children.map((child): ElementContent => {
        const node = child as unknown as RawLike;
        if (node.type === "raw" && BR_ONLY.test((node.value ?? "").trim())) {
          return { type: "element", tagName: "br", properties: {}, children: [] };
        }
        return child;
      });
    });
  };
}
