/** Focus and folding in the editor, as ProseMirror node decorations on each
 * top-level block (`.bn-block-outer`), so BlockNote's own DOM is never edited:
 *
 * - `data-fade`: Focus is on and the block is in another part (section
 *   headings and dividers stay);
 * - `data-part-body`: the block is in a part below a divider (it can fold);
 * - `data-folded`: that divider is folded;
 * - `data-card-hidden`: the note card shows one part (`only`) and the block
 *   is outside it (dividers and the section heading too: the card's title
 *   and folded lines stand for them).
 *
 * Parts follow `parts.ts` over the editor's own document, so they are right
 * on every keystroke. `refreshPartDecorations` redraws them after the view
 * state (Focus, the active part, folds) changes.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey, Selection, type EditorState, type Transaction } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";

import { partsOfNote, type PartBlock } from "./parts";

export interface PartsView {
  focusOn: boolean;
  activePart: string | null;
  folded: ReadonlySet<string>;
  /** The note card: the one part shown (the rest are hidden). */
  only?: string | null;
}

/** The plugin's state: bumped on each redraw request, and the note card's
 * one part (null: the whole note). */
interface PluginState {
  version: number;
  only: string | null;
}

const key = new PluginKey<PluginState>("paperhubNoteParts");

/** The view under the card's part, when there is one. */
function withOnly(view: PartsView, only: string | null): PartsView {
  return only === null ? view : { focusOn: false, activePart: null, folded: new Set(), only };
}

function toPartBlock(container: PMNode): PartBlock {
  const content = container.firstChild;
  return {
    id: String(container.attrs.id),
    type: content?.type.name ?? "paragraph",
    props: content?.attrs ?? {},
  };
}

interface Layout {
  attrs: Map<string, Record<string, string>>;
  top: { id: string; from: number; to: number }[];
}

/** Each top-level block's place and the attributes it takes under `view`. */
function layout(doc: PMNode, view: PartsView): Layout | null {
  const group = doc.firstChild;
  if (!group) return null;
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
  if (view.only != null) {
    const part = partsOfNote(flat).find((p) => p.id === view.only);
    const shown = new Set((part?.blocks ?? []).filter((b) => b.type !== "section").map((b) => b.id));
    for (const t of top) if (!shown.has(t.id)) attrs.set(t.id, { "data-card-hidden": "true" });
    return { attrs, top };
  }
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
  return { attrs, top };
}

/** The decorations for `doc` under `view`. */
export function partDecorations(doc: PMNode, view: PartsView): DecorationSet {
  const l = layout(doc, view);
  if (!l) return DecorationSet.empty;
  const decos = l.top.flatMap((t) => {
    const a = l.attrs.get(t.id);
    return a ? [Decoration.node(t.from, t.to, a)] : [];
  });
  return DecorationSet.create(doc, decos);
}

/** The document ranges of folded blocks, runs of them merged. */
export function foldedRanges(doc: PMNode, view: PartsView): [number, number][] {
  const l = layout(doc, view);
  const out: [number, number][] = [];
  for (const t of l?.top ?? []) {
    const a = l?.attrs.get(t.id);
    if (a?.["data-folded"] !== "true" && a?.["data-card-hidden"] !== "true") continue;
    const last = out[out.length - 1];
    if (last && last[1] === t.from) last[1] = t.to;
    else out.push([t.from, t.to]);
  }
  return out;
}

/** A selection moved out of a folded part: past it when it was moving down,
 * else back to the block before it (its divider). Null when it is not in one. */
export function skipFolded(state: EditorState, oldHead: number, view: PartsView): Transaction | null {
  const head = state.selection.head;
  const range = foldedRanges(state.doc, view).find(([from, to]) => head > from && head < to);
  if (!range) return null;
  const down = head >= oldHead;
  const target = down
    ? (Selection.findFrom(state.doc.resolve(range[1]), 1) ?? Selection.findFrom(state.doc.resolve(range[0]), -1))
    : (Selection.findFrom(state.doc.resolve(range[0]), -1) ?? Selection.findFrom(state.doc.resolve(range[1]), 1));
  return target ? state.tr.setSelection(target) : null;
}

/** The extension; `get` returns the current view state. `only`: the note
 * card's part, shown alone (`refreshPartDecorations` changes it). */
export function noteParts(get: () => PartsView, only: string | null = null) {
  let cache: { doc: PMNode; version: number; set: DecorationSet } | null = null;
  const viewOf = (state: EditorState) => withOnly(get(), key.getState(state)?.only ?? null);
  return createExtension({
    key: "paperhubNoteParts",
    prosemirrorPlugins: [
      new Plugin<PluginState>({
        key,
        state: {
          init: () => ({ version: 0, only }),
          apply: (tr, s) => {
            const meta = tr.getMeta(key) as { only?: string | null } | boolean | undefined;
            if (!meta) return s;
            const next = typeof meta === "object" && meta.only !== undefined ? meta.only : s.only;
            return { version: s.version + 1, only: next };
          },
        },
        // The caret never enters a folded part (its blocks are out of sight).
        appendTransaction: (trs, old, state) =>
          trs.some((t) => t.selectionSet || t.docChanged || t.getMeta(key))
            ? skipFolded(state, old.selection.head, viewOf(state))
            : null,
        props: {
          decorations(state) {
            const version = key.getState(state)?.version ?? 0;
            if (cache?.doc !== state.doc || cache.version !== version) {
              cache = { doc: state.doc, version, set: partDecorations(state.doc, viewOf(state)) };
            }
            return cache.set;
          },
        },
      }),
    ],
  });
}

/** Redraw the decorations after the view state changed (`only`: the note
 * card shows another part). */
export function refreshPartDecorations(view: EditorView | undefined, only?: string | null) {
  // An editor not mounted yet draws them on mount.
  if (!view || view.isDestroyed) return;
  view.dispatch(view.state.tr.setMeta(key, only === undefined ? true : { only }));
}
