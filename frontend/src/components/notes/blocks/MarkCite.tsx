import { createReactInlineContentSpec } from "@blocknote/react";
import { TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

import { MarkCiteView } from "./MarkCiteView";
import { turnCiteIntoBlock } from "./citeForms";

/** The text range of the block with this id: where its inline content starts and its size. */
export function textRange(view: EditorView, id: string): { start: number; size: number } | null {
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

function citeAt(view: EditorView, getPos: () => number | undefined): number | null {
  const pos = getPos();
  return typeof pos === "number" && view.state.doc.nodeAt(pos)?.type.name === "markCite" ? pos : null;
}

/** An inline citation of a reading mark: the user's short text (`text`, ""
 * = the quote's start) pointing at the mark `markId`. A new text mark with
 * a short quote lands as one at the note's caret; hovering it offers Turn
 * into reference block. Exported as its text and a footnote. */
export const markCite = createReactInlineContentSpec(
  {
    type: "markCite",
    propSchema: { markId: { default: "" }, text: { default: "" }, paperId: { default: 0 } },
    content: "none",
  },
  {
    render: ({ inlineContent, editor, getPos }) => {
      const { markId, text, paperId } = inlineContent.props;
      const view = () => editor._tiptapEditor.view;
      return (
        <MarkCiteView
          markId={markId}
          text={text}
          paperId={paperId}
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
          onTurnIntoBlock={(paper) => void turnCiteIntoBlock(editor, markId, paper, getPos)}
        />
      );
    },
  },
);
