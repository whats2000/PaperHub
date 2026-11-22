/** Folding in a note, as in Notion and Obsidian [user, 2026-11-22]:
 *
 * - a heading folds everything after it up to the next heading of the same
 *   or a higher level (an H2 folds its H3s and stops at the next H2 or H1);
 * - a paper § section block folds like an H1 (to the next section or H1);
 * - a list item (bullet, numbered, to-do) folds its nested items, and a
 *   heading's own nested blocks go with it.
 *
 * A divider folds nothing: it only bounds "one note" (parts.ts). Folds are
 * the viewer's (the store keeps the folded owners' ids per note); this file
 * only works out what they cover.
 */
import { marksInPart } from "./parts";

/** What folding reads of a block: an editor block, a stored row turned into
 * a tree, or a ProseMirror block container. */
export interface FoldNode {
  id: string;
  type: string;
  props?: Record<string, unknown>;
  children: FoldNode[];
}

const LIST_TYPES = new Set(["bulletListItem", "numberedListItem", "checkListItem"]);

/** A block's heading level for folding: a heading's level, 1 for a §
 * section, null for any other block. */
export function headingLevel(b: { type: string; props?: Record<string, unknown> }): number | null {
  if (b.type === "section") return 1;
  if (b.type !== "heading") return null;
  const level = Number(b.props?.level ?? 1);
  return Number.isFinite(level) ? level : 1;
}

/** Every foldable block and the blocks its fold hides (the top of the range:
 * blocks nested in them go with them), in document order. */
export function foldOwners(tree: FoldNode[]): Map<string, FoldNode[]> {
  const out = new Map<string, FoldNode[]>();
  const walk = (siblings: FoldNode[]) => {
    siblings.forEach((b, i) => {
      const level = headingLevel(b);
      if (level !== null) {
        // Blocks nested under the heading (Tab) go with it.
        const range: FoldNode[] = [...b.children];
        for (let j = i + 1; j < siblings.length; j++) {
          const other = headingLevel(siblings[j]!);
          if (other !== null && other <= level) break;
          range.push(siblings[j]!);
        }
        if (range.length) out.set(b.id, range);
      } else if (LIST_TYPES.has(b.type) && b.children.length) {
        out.set(b.id, b.children);
      }
      walk(b.children);
    });
  };
  walk(tree);
  return out;
}

/** The ids of the blocks hidden by the folds in `folded` (the top of each
 * folded range; their nested blocks are hidden with them). */
export function hiddenByFolds(tree: FoldNode[], folded: ReadonlySet<string>): Set<string> {
  const hidden = new Set<string>();
  if (!folded.size) return hidden;
  for (const [id, range] of foldOwners(tree)) {
    if (folded.has(id)) for (const b of range) hidden.add(b.id);
  }
  return hidden;
}

function flatten(list: FoldNode[], out: FoldNode[] = []): FoldNode[] {
  for (const b of list) {
    out.push(b);
    flatten(b.children, out);
  }
  return out;
}

/** What a folded block's chip says: the blocks it hides (nested ones too)
 * and the marks they quote, each once. */
export function foldSummaryOf(range: FoldNode[]): { blocks: number; marks: number } {
  const all = flatten(range);
  return { blocks: all.length, marks: marksInPart({ id: "", dividerId: null, blocks: all }).length };
}

/** Whether `blockId` is `b` or nested in it. */
function holds(b: FoldNode, blockId: string): boolean {
  return b.id === blockId || b.children.some((c) => holds(c, blockId));
}

/** The folded owners `blockId` sits under (outermost first): unfolding them
 * all brings it into view. */
export function foldersHiding(tree: FoldNode[], blockId: string, folded: ReadonlySet<string>): string[] {
  if (!folded.size) return [];
  const out: string[] = [];
  for (const [id, range] of foldOwners(tree)) {
    if (folded.has(id) && range.some((b) => holds(b, blockId))) out.push(id);
  }
  return out;
}

/** The fold the caret's block belongs to (Ctrl+Shift+[ / ]): the block
 * itself when it folds, else the innermost fold whose range holds it. */
export function caretFoldOwner(tree: FoldNode[], blockId: string): string | null {
  const owners = foldOwners(tree);
  if (owners.has(blockId)) return blockId;
  let best: string | null = null;
  // Owners are in document order, so a later one holding the block is
  // nested inside an earlier one (or a lower heading inside a higher one).
  for (const [id, range] of owners) if (range.some((b) => holds(b, blockId))) best = id;
  return best;
}

/** A stored note's rows (document order, `parent_id`) as a tree. */
export function treeFromRows(
  rows: readonly { id: string; parent_id: string | null; type: string; props?: Record<string, unknown> | null }[],
): FoldNode[] {
  const byId = new Map<string, FoldNode>();
  const roots: FoldNode[] = [];
  for (const r of rows) byId.set(r.id, { id: r.id, type: r.type, props: r.props ?? {}, children: [] });
  for (const r of rows) {
    const node = byId.get(r.id)!;
    const parent = r.parent_id ? byId.get(r.parent_id) : undefined;
    (parent ? parent.children : roots).push(node);
  }
  return roots;
}
