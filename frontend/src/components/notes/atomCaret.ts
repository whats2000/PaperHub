/** An insertion caret beside a block that takes no text (a paper reference,
 * a divider): a click on its left half puts a caret at its left edge, on its
 * right half at its right edge. Enter or a typed character then opens a new
 * line above (left) or below (right), so there is always a way to write
 * before a reference, even at the very top of the note.
 *
 * The block is node-selected (so the keys reach the editor and the browser
 * draws no text caret elsewhere); a node decoration draws the caret line
 * (`data-atom-caret` on the block's node view, styled in index.css). */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import {
  NodeSelection,
  Plugin,
  PluginKey,
  TextSelection,
  type EditorState,
} from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";

/** The blocks that get the caret (no text of their own, no inner editor). */
export const ATOM_CARET_TYPES: ReadonlySet<string> = new Set([
  "paperQuote",
  "paperEquation",
  "paperFigure",
  "paperRegion",
  "divider",
  "section",
  "toc",
]);

export type CaretSide = "before" | "after";

/** Where the caret is: the block's content node and the side. */
export interface AtomCaret {
  pos: number;
  side: CaretSide;
}

export const atomCaretKey = new PluginKey<AtomCaret | null>("paperhubAtomCaret");

/** Clicks on these keep their own behaviour and never set the caret. */
const INTERACTIVE =
  "button, a, input, textarea, select, summary, label, [role='button'], [role='link'], [role='menuitem'], [contenteditable='true']";

/** The blockContainer holding the block with this id, and its position. */
function findContainer(doc: PMNode, id: string): { node: PMNode; pos: number } | null {
  let found: { node: PMNode; pos: number } | null = null;
  doc.descendants((node, pos) => {
    if (found) return false;
    if (node.type.name === "blockContainer" && node.attrs.id === id) {
      found = { node, pos };
      return false;
    }
    return true;
  });
  return found;
}

function caretAt(state: EditorState, pos: number): PMNode | null {
  const node = state.doc.nodeAt(pos);
  return node && ATOM_CARET_TYPES.has(node.type.name) ? node : null;
}

/** Puts the caret beside a block (its content node at `pos`). */
export function setAtomCaret(view: EditorView, pos: number, side: CaretSide) {
  const tr = view.state.tr
    .setSelection(NodeSelection.create(view.state.doc, pos))
    .setMeta(atomCaretKey, { pos, side });
  view.dispatch(tr);
}

/** Opens an empty line (or one holding `text`) above or below the block and
 * puts the text caret in it. */
function openLine(view: EditorView, caret: AtomCaret, text?: string): boolean {
  const { state } = view;
  const $pos = state.doc.resolve(caret.pos);
  const container = $pos.parent;
  if (container.type.name !== "blockContainer") return false;
  const containerPos = $pos.before();
  const { blockContainer, paragraph } = state.schema.nodes;
  if (!blockContainer || !paragraph) return false;
  const line = blockContainer.create(
    null,
    paragraph.create(null, text ? state.schema.text(text) : null),
  );
  const at = caret.side === "before" ? containerPos : containerPos + container.nodeSize;
  const tr = state.tr.insert(at, line);
  // blockContainer > paragraph > text: the caret goes after what was typed.
  tr.setSelection(TextSelection.create(tr.doc, at + 2 + (text?.length ?? 0)));
  tr.setMeta(atomCaretKey, null);
  view.dispatch(tr.scrollIntoView());
  return true;
}

/** ArrowUp / ArrowDown: off the caret, onto the line above / below. */
function leave(view: EditorView, caret: AtomCaret, dir: "up" | "down"): boolean {
  const { state } = view;
  const $pos = state.doc.resolve(caret.pos);
  const containerPos = $pos.before();
  const target = dir === "up" ? containerPos : containerPos + $pos.parent.nodeSize;
  const sel = TextSelection.findFrom(state.doc.resolve(target), dir === "up" ? -1 : 1, true);
  const tr = state.tr.setMeta(atomCaretKey, null);
  if (sel) tr.setSelection(sel);
  view.dispatch(tr.scrollIntoView());
  return true;
}

/** A click on a block's left / right half puts the caret on that side;
 * a click on a control inside it, or anywhere else, hides the caret. */
function onBlockClick(view: EditorView, event: MouseEvent) {
  const el = event.target as HTMLElement | null;
  const clear = () => {
    if (atomCaretKey.getState(view.state)) view.dispatch(view.state.tr.setMeta(atomCaretKey, null));
  };
  const content = el?.closest<HTMLElement>(".bn-block-content");
  const type = content?.getAttribute("data-content-type");
  if (!el || !content || !type || !ATOM_CARET_TYPES.has(type)) return clear();
  // (The editor itself is contenteditable: only controls inside the block count.)
  const control = el.closest(INTERACTIVE);
  if (control && content.contains(control)) return clear();
  const id = content.closest(".bn-block-outer")?.getAttribute("data-id");
  const found = id ? findContainer(view.state.doc, id) : null;
  if (!found) return clear();
  const rect = content.getBoundingClientRect();
  const side: CaretSide = event.clientX < rect.left + rect.width / 2 ? "before" : "after";
  setAtomCaret(view, found.pos + 1, side);
  view.focus();
}

function isPrintable(e: KeyboardEvent): boolean {
  return e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing;
}

/** Delete at the end of a text line before a block that takes no text, or
 * Backspace at the start of one after it: never removes that block in one
 * keystroke (BlockNote's keymap would). An empty line goes; the caret goes
 * beside the block, on the side facing the line. */
function guardNeighbour(view: EditorView, event: KeyboardEvent): boolean {
  if (event.key !== "Delete" && event.key !== "Backspace") return false;
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return false;
  const { state } = view;
  const sel = state.selection;
  if (!(sel instanceof TextSelection) || !sel.empty) return false;
  const $c = sel.$cursor;
  if (!$c || !$c.parent.inlineContent || $c.depth < 2) return false;
  const containerDepth = $c.depth - 1;
  const container = $c.node(containerDepth);
  if (container.type.name !== "blockContainer") return false;
  const hasChildren = container.childCount > 1;
  const empty = $c.parent.content.size === 0;
  const containerPos = $c.before(containerDepth);
  if (event.key === "Delete") {
    if ($c.parentOffset !== $c.parent.content.size) return false;
    // The next block: the first child, else the next sibling.
    const nextPos = hasChildren
      ? containerPos + 1 + container.child(0).nodeSize + 1
      : $c.after(containerDepth);
    const next = state.doc.nodeAt(nextPos);
    if (next?.type.name !== "blockContainer" || !ATOM_CARET_TYPES.has(next.child(0).type.name))
      return false;
    if (empty && !hasChildren) {
      const tr = state.tr.delete(containerPos, containerPos + container.nodeSize);
      const atom = containerPos + 1;
      tr.setSelection(NodeSelection.create(tr.doc, atom)).setMeta(atomCaretKey, {
        pos: atom,
        side: "before",
      });
      view.dispatch(tr.scrollIntoView());
    } else {
      setAtomCaret(view, nextPos + 1, "before");
    }
    return true;
  }
  if ($c.parentOffset !== 0) return false;
  // The previous block in reading order: the deepest last child of the
  // previous sibling (no previous sibling: BlockNote un-nests; left alone).
  const $container = state.doc.resolve(containerPos);
  let prev = $container.nodeBefore;
  let prevPos = containerPos - (prev?.nodeSize ?? 0);
  while (prev?.type.name === "blockContainer" && prev.childCount > 1) {
    const group = prev.child(1);
    const groupPos = prevPos + 1 + prev.child(0).nodeSize;
    const last = group.lastChild;
    if (!last) break;
    prevPos = groupPos + 1 + group.content.size - last.nodeSize;
    prev = last;
  }
  if (prev?.type.name !== "blockContainer" || !ATOM_CARET_TYPES.has(prev.child(0).type.name))
    return false;
  if (empty && !hasChildren) {
    const tr = state.tr.delete(containerPos, containerPos + container.nodeSize);
    const atom = prevPos + 1;
    tr.setSelection(NodeSelection.create(tr.doc, atom)).setMeta(atomCaretKey, {
      pos: atom,
      side: "after",
    });
    view.dispatch(tr.scrollIntoView());
  } else {
    setAtomCaret(view, prevPos + 1, "after");
  }
  return true;
}

function onKey(view: EditorView, event: KeyboardEvent): boolean {
  if (event.defaultPrevented) return false;
  const caret = atomCaretKey.getState(view.state);
  if (!caret || !caretAt(view.state, caret.pos)) return guardNeighbour(view, event);
  // Alt+↑ / Alt+↓ move the block (the notes keymap).
  if (event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) return false;
  switch (event.key) {
    case "Enter":
      if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return false;
      return openLine(view, caret);
    case "ArrowLeft":
    case "ArrowRight":
      setAtomCaret(view, caret.pos, event.key === "ArrowLeft" ? "before" : "after");
      return true;
    case "ArrowUp":
      return leave(view, caret, "up");
    case "ArrowDown":
      return leave(view, caret, "down");
    case "Escape":
      view.dispatch(view.state.tr.setMeta(atomCaretKey, null));
      return true;
    case "Backspace":
      // Deletes the (selected) block as before, from its right side
      // only; from its left side there is nothing before the caret.
      return caret.side === "before";
    case "Delete":
      return caret.side === "after";
    default:
      if (isPrintable(event)) return openLine(view, caret, event.key);
      return false;
  }
}

export const atomCaret = createExtension({
  key: "paperhubAtomCaret",
  prosemirrorPlugins: [
    new Plugin<AtomCaret | null>({
      key: atomCaretKey,
      state: {
        init: () => null,
        apply: (tr, value, _old, state) => {
          const meta = tr.getMeta(atomCaretKey) as AtomCaret | null | undefined;
          if (meta !== undefined) return meta;
          if (!value) return null;
          const pos = tr.docChanged ? tr.mapping.map(value.pos) : value.pos;
          // Any other move of the selection (a re-read of the same node
          // selection, e.g. on focus, keeps it), or losing the block, ends it.
          const sel = state.selection;
          if (tr.selectionSet && !(sel instanceof NodeSelection && sel.from === pos)) return null;
          return caretAt(state, pos) ? { ...value, pos } : null;
        },
      },
      // A native listener: the blocks' node views stop click events before
      // ProseMirror's own handlers see them.
      view: (view) => {
        const onClick = (event: MouseEvent) => onBlockClick(view, event);
        view.dom.addEventListener("click", onClick);
        return { destroy: () => view.dom.removeEventListener("click", onClick) };
      },
      props: {
        decorations: (state) => {
          const caret = atomCaretKey.getState(state);
          const node = caret ? caretAt(state, caret.pos) : null;
          if (!caret || !node) return null;
          return DecorationSet.create(state.doc, [
            Decoration.node(caret.pos, caret.pos + node.nodeSize, {
              "data-atom-caret": caret.side,
            }),
          ]);
        },
        handleDOMEvents: {
          // Ahead of BlockNote's own keymap, whose Delete / Backspace would
          // remove a neighbouring reference from a text line.
          keydown: (view, event) => {
            if (!onKey(view, event)) return false;
            event.preventDefault();
            return true;
          },
          blur: (view) => {
            if (atomCaretKey.getState(view.state))
              view.dispatch(view.state.tr.setMeta(atomCaretKey, null));
            return false;
          },
        },
        handleKeyDown: (view, event) => onKey(view, event),
      },
    }),
  ],
});
