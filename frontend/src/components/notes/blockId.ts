/** Pure helpers over a notes editor document (no store, no UI). */
import type { Node as PMNode } from "@tiptap/pm/model";

/** The id of the block whose text holds position `pos` of `doc` (a table
 * cell's text belongs to the table block). */
export function blockIdInDoc(doc: PMNode, pos: number): string | null {
  const $pos = doc.resolve(pos);
  for (let d = $pos.depth; d >= 0; d--) {
    const node = $pos.node(d);
    if (node.type.name === "blockContainer") return String(node.attrs.id);
  }
  return null;
}
