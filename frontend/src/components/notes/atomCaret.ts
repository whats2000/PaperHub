/** An insertion caret beside an object block (a paper reference, a divider,
 * a table, a code or math block, …): a click on its left half puts a caret at
 * its left edge, on its right half at its right edge (on a block edited in
 * place, a click beside its content or in its gutter). Enter or a typed
 * character then opens a new line above (left) or below (right), so there is
 * always a way to write before a reference, even at the very top of the note.
 *
 * The block is node-selected (so the keys reach the editor and the browser
 * draws no text caret elsewhere); a node decoration marks it
 * (`data-object-selected`: a light tint, no frame) and draws the caret line
 * (`data-atom-caret`), both styled in index.css. */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import {
  NodeSelection,
  Plugin,
  PluginKey,
  Selection,
  TextSelection,
  type EditorState,
} from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";

/** The blocks that take no text of their own: Delete / Backspace from a
 * text line never remove one in a keystroke. */
const NO_TEXT_TYPES: ReadonlySet<string> = new Set([
  "paperQuote",
  "paperEquation",
  "paperFigure",
  "paperRegion",
  "divider",
  "section",
  "toc",
  "math",
]);

/** Object blocks whose content is edited in place, and the element that
 * holds it: a click there edits; beside it, the caret. */
const EDITED_IN_PLACE: Readonly<Record<string, string>> = {
  table: "table",
  codeBlock: "pre",
  math: "[data-math-render]",
};

/** Every object block (anything that is not a line of text) gets the caret:
 * a later block type that is not a line of text is added here. */
export const ATOM_CARET_TYPES: ReadonlySet<string> = new Set([
  ...NO_TEXT_TYPES,
  ...Object.keys(EDITED_IN_PLACE),
]);

/** The block's own gutter, left and right of its content: the selected
 * block's tint spans it, the caret is drawn in it, a click in it counts as a
 * click beside the block (index.css draws the same width). */
export const ATOM_GUTTER_PX = 8;

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

/** The table plugin turns a node selection of a table into a selection of
 * all its cells (BlockNote gives it no option to keep the node): for a
 * table, that is the block selected. */
function selectsWholeTable(sel: Selection, pos: number): boolean {
  const cells = sel as Selection & {
    $anchorCell?: { depth: number; before: (depth: number) => number };
    isRowSelection?: () => boolean;
    isColSelection?: () => boolean;
  };
  const $cell = cells.$anchorCell;
  if (!$cell || !cells.isRowSelection?.() || !cells.isColSelection?.()) return false;
  return $cell.before($cell.depth - 1) === pos;
}

/** Whether the selection is the block whose content node is at `pos`. */
function selectsBlock(sel: Selection, pos: number): boolean {
  return (sel instanceof NodeSelection && sel.from === pos) || selectsWholeTable(sel, pos);
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

/** The object block whose gutter (the strip ATOM_GUTTER_PX wide left or
 * right of its content) holds this point, and the side. */
function inGutter(view: EditorView, x: number, y: number): { content: HTMLElement; side: CaretSide } | null {
  let hit: { content: HTMLElement; side: CaretSide } | null = null;
  for (const content of view.dom.querySelectorAll<HTMLElement>(".bn-block-content[data-content-type]")) {
    if (!ATOM_CARET_TYPES.has(content.getAttribute("data-content-type") ?? "")) continue;
    const r = content.getBoundingClientRect();
    if (y < r.top || y > r.bottom) continue;
    // The innermost (a nested block comes later in the document) wins.
    if (x < r.left && x >= r.left - ATOM_GUTTER_PX) hit = { content, side: "before" };
    else if (x > r.right && x <= r.right + ATOM_GUTTER_PX) hit = { content, side: "after" };
  }
  return hit;
}

/** A click on a block's left / right half puts the caret on that side (on
 * a block edited in place: beside its content, not on it), as does a click
 * in its gutter; a click on a control inside it, or anywhere else, hides
 * the caret. */
function onBlockClick(view: EditorView, event: MouseEvent) {
  const el = event.target as HTMLElement | null;
  const clear = () => {
    if (atomCaretKey.getState(view.state)) view.dispatch(view.state.tr.setMeta(atomCaretKey, null));
  };
  if (!el) return clear();
  let content = el.closest<HTMLElement>(".bn-block-content");
  let side: CaretSide | null = null;
  if (!content) {
    // Outside every block's content: the editor's margin, maybe a gutter.
    const hit = inGutter(view, event.clientX, event.clientY);
    if (!hit) return clear();
    ({ content, side } = hit);
  }
  const type = content.getAttribute("data-content-type");
  if (!type || !ATOM_CARET_TYPES.has(type)) return clear();
  // (The editor itself is contenteditable: only controls inside the block count.)
  const control = el.closest(INTERACTIVE);
  if (control && content.contains(control)) return clear();
  const inner = EDITED_IN_PLACE[type];
  const held = inner ? content.querySelector<HTMLElement>(inner) : null;
  if (side === null && held) {
    // A click on the content edits it (the editor's own handling).
    if (held.contains(el)) return clear();
    const r = held.getBoundingClientRect();
    side = event.clientX < r.left + r.width / 2 ? "before" : "after";
  }
  const id = content.closest(".bn-block-outer")?.getAttribute("data-id");
  const found = id ? findContainer(view.state.doc, id) : null;
  if (!found) return clear();
  if (side === null) {
    const rect = content.getBoundingClientRect();
    side = event.clientX < rect.left + rect.width / 2 ? "before" : "after";
  }
  setAtomCaret(view, found.pos + 1, side);
  view.focus();
}

/** A table's caret holds all its cells selected (the table plugin's form of
 * a selected table), where the editor's Backspace / Delete would only empty
 * them: the block goes, as any other selected block does. */
function deleteTable(view: EditorView, caret: AtomCaret): boolean {
  const { state } = view;
  if (state.doc.nodeAt(caret.pos)?.type.name !== "table") return false;
  const $pos = state.doc.resolve(caret.pos);
  const from = $pos.before();
  const tr = state.tr.delete(from, from + $pos.parent.nodeSize).setMeta(atomCaretKey, null);
  const near = Selection.findFrom(tr.doc.resolve(Math.min(from, tr.doc.content.size)), caret.side === "after" ? -1 : 1, true);
  if (near) tr.setSelection(near);
  view.dispatch(tr.scrollIntoView());
  return true;
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
    if (next?.type.name !== "blockContainer" || !NO_TEXT_TYPES.has(next.child(0).type.name))
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
  if (prev?.type.name !== "blockContainer" || !NO_TEXT_TYPES.has(prev.child(0).type.name))
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
      if (caret.side === "before") return true;
      return deleteTable(view, caret);
    case "Delete":
      if (caret.side === "after") return true;
      return deleteTable(view, caret);
    default:
      if (isPrintable(event)) return openLine(view, caret, event.key);
      return false;
  }
}

/** Views whose selection is being moved off an opening atom right now. */
const opening = new WeakSet<EditorView>();

/** Whether the selection change under way only moves a note's opening
 * selection off an atom block: no caret of the user's, so it names no
 * part you are in and no block you were at. */
export function isOpeningMove(view: EditorView): boolean {
  return opening.has(view);
}

/** An opening node selection (the editor not focused, no caret beside the
 * block) moved to the document's first text position. */
function unselectOpeningAtom(view: EditorView) {
  const { selection } = view.state;
  if (!(selection instanceof NodeSelection) || view.hasFocus() || atomCaretKey.getState(view.state)) return;
  const text = Selection.findFrom(view.state.doc.resolve(0), 1, true);
  if (!text) return;
  opening.add(view);
  try {
    view.dispatch(view.state.tr.setSelection(text).setMeta("addToHistory", false));
  } finally {
    opening.delete(view);
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
          if (tr.selectionSet && !selectsBlock(sel, pos)) return null;
          return caretAt(state, pos) ? { ...value, pos } : null;
        },
      },
      // A native listener: the blocks' node views stop click events before
      // ProseMirror's own handlers see them.
      view: (view) => {
        const onClick = (event: MouseEvent) => onBlockClick(view, event);
        view.dom.addEventListener("click", onClick);
        let live = true;
        // A note whose first block takes no text (a table of contents)
        // opens with that block node-selected (ProseMirror's start of the
        // document) and drawn framed as if the user had picked it: opening
        // a note selects nothing, so the selection moves to the first text.
        queueMicrotask(() => {
          if (live) unselectOpeningAtom(view);
        });
        return {
          destroy: () => {
            live = false;
            view.dom.removeEventListener("click", onClick);
          },
        };
      },
      props: {
        // A selected object block is tinted (no frame), with the caret
        // beside it when there is one (index.css).
        decorations: (state) => {
          const sel = state.selection;
          const caret = atomCaretKey.getState(state);
          const pos = caret?.pos ?? (sel instanceof NodeSelection ? sel.from : -1);
          const node = pos >= 0 ? caretAt(state, pos) : null;
          if (!node) return null;
          const attrs: Record<string, string> = {};
          if (selectsBlock(sel, pos)) attrs["data-object-selected"] = "";
          if (caret) attrs["data-atom-caret"] = caret.side;
          return DecorationSet.create(state.doc, [Decoration.node(pos, pos + node.nodeSize, attrs)]);
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
