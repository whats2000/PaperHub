import { createReactInlineContentSpec } from "@blocknote/react";
import { TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

import { MarkCiteView } from "./MarkCiteView";

// The citation just inserted opens its text field; one already in a note
// does not take the caret.
let openNext: string | null = null;

/** Blocks whose text can hold an inline citation. */
const TEXT_BLOCKS = new Set([
  "paragraph",
  "heading",
  "bulletListItem",
  "numberedListItem",
  "checkListItem",
  "quote",
  "callout",
]);

export interface MarkCiteTarget {
  markId: string;
  /** The mark's paper (0: the note's paper). */
  paperId: number;
  /** The block the caret was last in, and where in its text (unknown: its end). */
  blockId: string | null;
  offset?: number | null;
  /** No usable caret block: a new line after this block (else the note's end). */
  fallbackAfter?: string | null;
}

interface CiteEditor {
  _tiptapEditor: { view: EditorView };
  document: { id: string }[];
  getBlock: (id: string) => { id: string; type: string } | undefined;
  insertInlineContent: (c: never) => void;
  insertBlocks: (blocks: never, ref: string, placement: "before" | "after") => unknown;
}

/** The text range of the block with this id: where its inline content starts and its size. */
function textRange(view: EditorView, id: string): { start: number; size: number } | null {
  let found: { start: number; size: number } | null = null;
  view.state.doc.descendants((node, pos) => {
    if (found) return false;
    if (node.type.name === "blockContainer" && node.attrs.id === id) {
      const content = node.firstChild;
      if (content) found = { start: pos + 2, size: content.content.size };
      return false;
    }
    return true;
  });
  return found;
}

/** Inserts an inline citation of a mark at the remembered caret (its exact
 * offset if known, else the end of that block); with no text block to go
 * into, on a new line after `fallbackAfter` (else at the note's end). Its
 * text field opens with the default selected, so typing replaces it. */
export function insertMarkCite(editor: CiteEditor, target: MarkCiteTarget): void {
  const node = { type: "markCite", props: { markId: target.markId, text: "", paperId: target.paperId } };
  const view = editor._tiptapEditor.view;
  const block = target.blockId ? editor.getBlock(target.blockId) : undefined;
  const range = block && TEXT_BLOCKS.has(block.type) ? textRange(view, block.id) : null;
  openNext = target.markId;
  if (block && range) {
    const off = target.offset ?? range.size;
    const at = range.start + Math.max(0, Math.min(off, range.size));
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, at)));
    editor.insertInlineContent([node] as never);
    return;
  }
  const after =
    block?.id ??
    (target.fallbackAfter && editor.getBlock(target.fallbackAfter) ? target.fallbackAfter : null) ??
    editor.document[editor.document.length - 1]?.id;
  if (!after) return;
  editor.insertBlocks([{ type: "paragraph", content: [node] }] as never, after, "after");
}

function citeAt(view: EditorView, getPos: () => number | undefined): number | null {
  const pos = getPos();
  return typeof pos === "number" && view.state.doc.nodeAt(pos)?.type.name === "markCite" ? pos : null;
}

/** An inline citation of a reading mark: the user's short text (`text`, ""
 * = the quote's start) pointing at the mark `markId`. Made from the note
 * card's "Insert as inline citation"; exported as its text and a footnote. */
export const markCite = createReactInlineContentSpec(
  {
    type: "markCite",
    propSchema: { markId: { default: "" }, text: { default: "" }, paperId: { default: 0 } },
    content: "none",
  },
  {
    render: ({ inlineContent, editor, getPos }) => {
      const { markId, text, paperId } = inlineContent.props;
      // Read, not cleared, here: StrictMode renders twice. The view clears it.
      const autoEdit = openNext === markId && text === "";
      const view = () => editor._tiptapEditor.view;
      return (
        <MarkCiteView
          markId={markId}
          text={text}
          paperId={paperId}
          autoEdit={autoEdit}
          onMounted={() => {
            if (openNext === markId) openNext = null;
          }}
          onCommit={(next) => {
            const v = view();
            const pos = citeAt(v, getPos);
            if (pos === null) return;
            const attrs = v.state.doc.nodeAt(pos)!.attrs;
            v.dispatch(v.state.tr.setNodeMarkup(pos, undefined, { ...attrs, text: next }));
          }}
          onLeave={(side) => {
            const v = view();
            const pos = citeAt(v, getPos);
            v.focus();
            if (pos === null) return;
            const at = side === "before" ? pos : pos + 1;
            v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, at)));
          }}
        />
      );
    },
  },
);
