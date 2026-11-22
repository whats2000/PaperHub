/** Focus and folding in the editor, as ProseMirror decorations, so
 * BlockNote's own DOM is never edited:
 *
 * - `data-fade`: Focus is on and the top-level block is in another part
 *   (section headings and dividers stay);
 * - `data-card-hidden`: the note card shows one part (`only`) and the block
 *   is outside it (dividers and the section heading too: the card's title
 *   and folded lines stand for them);
 * - `data-fold-owner` (`open` / `folded`): the block folds (folds.ts: a
 *   heading, a § section, a list item with nested items);
 * - `data-fold-body`: the block is in a fold's range (it can collapse);
 * - `data-folded`: that fold is closed and hides the block.
 *
 * A foldable block also gets two widgets, host elements that `FoldControls`
 * renders into: its toggle (inside the block, before its content: it hangs
 * in the left gutter) and, while folded, its "… 4 blocks · 2 marks" chip
 * (after its text; a § section draws its own).
 *
 * Parts follow `parts.ts` and folds `folds.ts` over the editor's own
 * document, so they are right on every keystroke. `refreshPartDecorations`
 * redraws them after the view state (Focus, the active part, folds)
 * changes.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey, Selection, type EditorState, type Transaction } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";

import { prefersReducedMotion } from "@/lib/motion";

import { FOLD_MS } from "./foldMotion";
import { foldOwners, hiddenByFolds, type FoldNode } from "./folds";
import { partsOfNote, type PartBlock } from "./parts";

export interface PartsView {
  focusOn: boolean;
  activePart: string | null;
  /** The folded blocks' ids (headings, sections, list items). */
  folded: ReadonlySet<string>;
  /** The note card: the one part shown (the rest are hidden). */
  only?: string | null;
  /** The blocks drawing a fold chip: the folded ones, and ones just
   * unfolded while their chip fades out (default: `folded`). */
  chips?: ReadonlySet<string>;
}

/** The elements the fold toggles and chips are rendered into, one per
 * block, the same one every time (a redrawn widget keeps what React put in
 * it). */
export interface FoldHosts {
  toggle: (blockId: string) => HTMLElement;
  chip: (blockId: string) => HTMLElement;
  /** Lets go of the hosts of blocks that no longer fold. */
  keep: (blockIds: ReadonlySet<string>) => void;
  /** The blocks drawing a chip for `folded`: those folded, and those just
   * unfolded while their chip fades out (for FOLD_MS; then `onExpire`). */
  chips: (folded: ReadonlySet<string>) => ReadonlySet<string>;
  /** What redraws the decorations once a chip has faded out (FoldControls). */
  setOnExpire: (fn: (() => void) | undefined) => void;
}

export function createFoldHosts(): FoldHosts {
  const hosts = new Map<string, HTMLElement>();
  const get = (key: string, className: string) => {
    let el = hosts.get(key);
    if (!el) {
      el = document.createElement("span");
      el.className = className;
      el.contentEditable = "false";
      hosts.set(key, el);
    }
    return el;
  };
  let last: ReadonlySet<string> = new Set();
  const leaving = new Set<string>();
  let onExpire: (() => void) | undefined;
  return {
    setOnExpire(fn) {
      onExpire = fn;
    },
    chips(folded) {
      const gone = [...last].filter((id) => !folded.has(id));
      last = folded;
      if (gone.length && !prefersReducedMotion()) {
        gone.forEach((id) => leaving.add(id));
        setTimeout(() => {
          gone.forEach((id) => leaving.delete(id));
          onExpire?.();
        }, FOLD_MS);
      }
      for (const id of folded) leaving.delete(id);
      return leaving.size ? new Set([...folded, ...leaving]) : folded;
    },
    toggle: (id) => get(`t:${id}`, "note-fold-host"),
    chip: (id) => get(`c:${id}`, "note-fold-chip-host"),
    keep(ids) {
      for (const key of [...hosts.keys()]) if (!ids.has(key.slice(2))) hosts.delete(key);
    },
  };
}

/** The plugin's state: bumped on each redraw request, and the note card's
 * one part (null: the whole note). */
interface PluginState {
  version: number;
  only: string | null;
}

const key = new PluginKey<PluginState>("paperhubNoteParts");

/** The view under the card's part, when there is one (the card never folds). */
function withOnly(view: PartsView, only: string | null): PartsView {
  return only === null ? view : { focusOn: false, activePart: null, folded: new Set(), only };
}

/** A block container: where it sits, its content node and nested blocks. */
interface Container {
  id: string;
  from: number;
  to: number;
  content: PMNode | null;
  top: boolean;
  fold: FoldNode;
}

/** Every block container of `doc` in document order, and its fold tree. */
function containersOf(doc: PMNode): { all: Container[]; tree: FoldNode[] } {
  const all: Container[] = [];
  const visit = (group: PMNode, start: number, top: boolean): FoldNode[] => {
    const out: FoldNode[] = [];
    group.forEach((node, offset) => {
      if (node.type.name !== "blockContainer") return;
      const from = start + offset;
      const content = node.firstChild;
      const fold: FoldNode = {
        id: String(node.attrs.id),
        type: content?.type.name ?? "paragraph",
        props: content?.attrs ?? {},
        children: [],
      };
      all.push({ id: fold.id, from, to: from + node.nodeSize, content, top, fold });
      // The nested group follows the content, inside the container.
      const nested = node.childCount > 1 ? node.child(1) : null;
      if (nested && content) fold.children = visit(nested, from + 1 + content.nodeSize + 1, false);
      out.push(fold);
    });
    return out;
  };
  const group = doc.firstChild;
  const tree = group ? visit(group, 1, true) : [];
  return { all, tree };
}

interface Layout {
  attrs: Map<string, Record<string, string>>;
  all: Container[];
  /** The foldable blocks (none on the note card). */
  owners: Set<string>;
}

/** Each block's place and the attributes it takes under `view`. */
function layout(doc: PMNode, view: PartsView): Layout | null {
  if (!doc.firstChild) return null;
  const { all, tree } = containersOf(doc);
  const attrs = new Map<string, Record<string, string>>();
  const set = (id: string, name: string, value: string) => {
    const a = attrs.get(id) ?? {};
    a[name] = value;
    attrs.set(id, a);
  };
  // The parts rule reads every block in document order.
  const flat: PartBlock[] = all.map((c) => ({ id: c.id, type: c.fold.type, props: c.fold.props }));
  const top = all.filter((c) => c.top);
  if (view.only != null) {
    const part = partsOfNote(flat).find((p) => p.id === view.only);
    const shown = new Set((part?.blocks ?? []).filter((b) => b.type !== "section").map((b) => b.id));
    for (const t of top) if (!shown.has(t.id)) set(t.id, "data-card-hidden", "true");
    return { attrs, all, owners: new Set() };
  }
  if (view.focusOn && view.activePart !== null) {
    const topIds = new Set(top.map((t) => t.id));
    for (const part of partsOfNote(flat)) {
      if (part.id === view.activePart) continue;
      for (const b of part.blocks) if (b.type !== "section" && topIds.has(b.id)) set(b.id, "data-fade", "true");
    }
  }
  const ranges = foldOwners(tree);
  const hidden = hiddenByFolds(tree, view.folded);
  for (const [id, range] of ranges) {
    set(id, "data-fold-owner", view.folded.has(id) ? "folded" : "open");
    for (const b of range) set(b.id, "data-fold-body", "");
  }
  for (const id of hidden) set(id, "data-folded", "true");
  return { attrs, all, owners: new Set(ranges.keys()) };
}

const WIDGET = { ignoreSelection: true, stopEvent: () => true, marks: [] };

/** The decorations for `doc` under `view` (`hosts`: where the fold toggles
 * and chips go; none: no fold controls). */
export function partDecorations(doc: PMNode, view: PartsView, hosts?: FoldHosts): DecorationSet {
  const l = layout(doc, view);
  if (!l) return DecorationSet.empty;
  const chips = view.chips ?? view.folded;
  const decos: Decoration[] = [];
  for (const c of l.all) {
    const a = l.attrs.get(c.id);
    if (a) decos.push(Decoration.node(c.from, c.to, a));
    if (!hosts || !l.owners.has(c.id)) continue;
    decos.push(Decoration.widget(c.from + 1, () => hosts.toggle(c.id), { ...WIDGET, side: -1, key: `fold:${c.id}` }));
    // The chip follows the block's text (a § section draws its own).
    if (chips.has(c.id) && c.content?.isTextblock) {
      const end = c.from + 2 + c.content.content.size;
      decos.push(Decoration.widget(end, () => hosts.chip(c.id), { ...WIDGET, side: 1, key: `foldchip:${c.id}` }));
    }
  }
  hosts?.keep(l.owners);
  return DecorationSet.create(doc, decos);
}

/** The document ranges of folded blocks, runs of them merged. */
export function foldedRanges(doc: PMNode, view: PartsView): [number, number][] {
  const l = layout(doc, view);
  const out: [number, number][] = [];
  for (const c of l?.all ?? []) {
    const a = l?.attrs.get(c.id);
    if (a?.["data-folded"] !== "true" && a?.["data-card-hidden"] !== "true") continue;
    const last = out[out.length - 1];
    // Nested in a range already taken, or right after it.
    if (last && c.from <= last[1]) last[1] = Math.max(last[1], c.to);
    else out.push([c.from, c.to]);
  }
  return out;
}

/** A selection moved out of a folded range: past it when it was moving
 * down, else back to the block before it. Null when it is not in one. */
export function skipFolded(state: EditorState, oldHead: number, view: PartsView): Transaction | null {
  // Nothing folded or hidden: nothing to skip, and no layout to work out
  // (this runs on every selection change and keystroke).
  if (!view.folded.size && view.only == null) return null;
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
 * card's part, shown alone (`refreshPartDecorations` changes it). `hosts`:
 * where the fold toggles and chips are rendered. */
export function noteParts(get: () => PartsView, only: string | null = null, hosts?: FoldHosts) {
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
        // The caret never enters a folded range (its blocks are out of sight).
        appendTransaction: (trs, old, state) =>
          trs.some((t) => t.selectionSet || t.docChanged || t.getMeta(key))
            ? skipFolded(state, old.selection.head, viewOf(state))
            : null,
        props: {
          decorations(state) {
            const version = key.getState(state)?.version ?? 0;
            if (cache?.doc !== state.doc || cache.version !== version) {
              const view = viewOf(state);
              cache = { doc: state.doc, version, set: partDecorations(state.doc, view, view.only == null ? hosts : undefined) };
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
