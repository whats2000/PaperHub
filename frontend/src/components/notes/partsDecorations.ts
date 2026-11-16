/** Focus and folding in the editor, as ProseMirror node decorations on each
 * top-level block (`.bn-block-outer`), so BlockNote's own DOM is never edited:
 *
 * - `data-fade`: Focus is on and the block is in another part (section
 *   headings and dividers stay);
 * - `data-part-body`: the block is in a part below a divider (it can fold);
 * - `data-folded`: that divider is folded.
 *
 * Parts follow `parts.ts` over the editor's own document, so they are right
 * on every keystroke. `refreshPartDecorations` redraws them after the view
 * state (Focus, the active part, folds) changes.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";

import { partsOfNote, type PartBlock } from "./parts";

export interface PartsView {
  focusOn: boolean;
  activePart: string | null;
  folded: ReadonlySet<string>;
}

const key = new PluginKey<number>("paperhubNoteParts");

function toPartBlock(container: PMNode): PartBlock {
  const content = container.firstChild;
  return {
    id: String(container.attrs.id),
    type: content?.type.name ?? "paragraph",
    props: content?.attrs ?? {},
  };
}

/** The decorations for `doc` under `view`. */
export function partDecorations(doc: PMNode, view: PartsView): DecorationSet {
  const group = doc.firstChild;
  if (!group) return DecorationSet.empty;
  // Every block in document order (the rule reads nested dividers too), and
  // where each top-level block sits.
  const flat: PartBlock[] = [];
  const top: { id: string; from: number; to: number }[] = [];
  group.forEach((node, offset) => {
    const from = 1 + offset;
    top.push({ id: String(node.attrs.id), from, to: from + node.nodeSize });
    flat.push(toPartBlock(node));
    node.descendants((child) => {
      if (child.type.name === "blockContainer") flat.push(toPartBlock(child));
      return true;
    });
  });
  const attrs = new Map<string, Record<string, string>>();
  for (const part of partsOfNote(flat)) {
    const fade = view.focusOn && view.activePart !== null && part.id !== view.activePart;
    const folded = part.dividerId !== null && view.folded.has(part.dividerId);
    for (const b of part.blocks) {
      const a: Record<string, string> = {};
      if (fade && b.type !== "section") a["data-fade"] = "true";
      if (part.dividerId !== null) a["data-part-body"] = "";
      if (folded) a["data-folded"] = "true";
      if (Object.keys(a).length) attrs.set(b.id, a);
    }
  }
  const decos = top.flatMap((t) => {
    const a = attrs.get(t.id);
    return a ? [Decoration.node(t.from, t.to, a)] : [];
  });
  return DecorationSet.create(doc, decos);
}

/** The extension; `get` returns the current view state. */
export function noteParts(get: () => PartsView) {
  let cache: { doc: PMNode; version: number; set: DecorationSet } | null = null;
  return createExtension({
    key: "paperhubNoteParts",
    prosemirrorPlugins: [
      new Plugin<number>({
        key,
        state: {
          init: () => 0,
          apply: (tr, version) => (tr.getMeta(key) ? version + 1 : version),
        },
        props: {
          decorations(state) {
            const version = key.getState(state) ?? 0;
            if (cache?.doc !== state.doc || cache.version !== version) {
              cache = { doc: state.doc, version, set: partDecorations(state.doc, get()) };
            }
            return cache.set;
          },
        },
      }),
    ],
  });
}

/** Redraw the decorations after the view state changed. */
export function refreshPartDecorations(view: EditorView | undefined) {
  // An editor not mounted yet draws them on mount.
  if (!view || view.isDestroyed) return;
  view.dispatch(view.state.tr.setMeta(key, true));
}
