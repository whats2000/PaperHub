/** Empty lines a note editor holds that the server never had (a fresh
 * Enter is not sent while empty, `pruneUnsavedEmpty`): a reload of the
 * note replaces the whole document with the server's and would take them
 * away, the caret's line with them. They are put back where they stood.
 * [user, 2026-11-23] */

import { treeIds, type EditorBlock } from "@/components/notes/sync";

/** An unsent empty line and its neighbours where it stood. */
interface LooseLine {
  id: string;
  after: string | null;
  before: string | null;
}

/** What `replaceKeepingLines` uses of the editor. */
interface LineOps {
  document: unknown;
  getBlock: (id: string) => { id: string } | undefined;
  getTextCursorPosition: () => { block: { id: string } };
  setTextCursorPosition: (id: string, placement: "start" | "end") => unknown;
  replaceBlocks: (remove: never, insert: never) => unknown;
  insertBlocks: (blocks: never, ref: string, placement: "before" | "after") => unknown;
}

function isEmpty(c: unknown): boolean {
  return c === undefined || c === null || c === "" || (Array.isArray(c) && c.length === 0);
}

/** The empty leaf paragraphs of `tree` not in `known`, in document order. */
function looseLines(tree: EditorBlock[], known: ReadonlySet<string>, out: LooseLine[] = []): LooseLine[] {
  tree.forEach((b, i) => {
    if (b.type === "paragraph" && !known.has(b.id) && !b.children.length && isEmpty(b.content)) {
      out.push({ id: b.id, after: tree[i - 1]?.id ?? null, before: tree[i + 1]?.id ?? null });
    }
    looseLines(b.children, known, out);
  });
  return out;
}

/** Replaces the editor's document with `tree` (the server's, with the edits
 * not yet saved on top), keeping the unsent empty lines next to the block
 * they followed (else preceded); the caret stays on its line. `known`: the
 * ids the server has or will have. */
export function replaceKeepingLines(editor: LineOps, tree: EditorBlock[], known: ReadonlySet<string>): void {
  const keep = tree.length
    ? looseLines(editor.document as EditorBlock[], new Set([...known, ...treeIds(tree)]))
    : [];
  let caret: string | null = null;
  try {
    caret = editor.getTextCursorPosition().block.id;
  } catch {
    /* no caret */
  }
  editor.replaceBlocks(editor.document as never, (tree.length ? tree : [{ type: "paragraph" }]) as never);
  for (const line of keep) {
    const block = [{ id: line.id, type: "paragraph" }] as never;
    if (line.after && editor.getBlock(line.after)) editor.insertBlocks(block, line.after, "after");
    else if (line.before && editor.getBlock(line.before)) editor.insertBlocks(block, line.before, "before");
  }
  if (caret && keep.some((l) => l.id === caret) && editor.getBlock(caret)) {
    editor.setTextCursorPosition(caret, "start");
  }
}
