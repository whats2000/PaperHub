import { createReactInlineContentSpec } from "@blocknote/react";
import { TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

import { InlineMathView } from "./InlineMathView";

// The inline equation the slash menu just put in opens for editing; one
// already in a note (even empty) does not take the caret.
let openNext = false;

/** Insert an empty inline equation at the caret, opened for editing. */
export function insertInlineMath(editor: { insertInlineContent: (c: never) => void }): void {
  openNext = true;
  editor.insertInlineContent([{ type: "inlineMath", props: { latex: "" } }, " "] as never);
}

function inlineMathAt(view: EditorView, getPos: () => number | undefined): number | null {
  const pos = getPos();
  return typeof pos === "number" && view.state.doc.nodeAt(pos)?.type.name === "inlineMath" ? pos : null;
}

/** An equation inside the sentence (LaTeX, KaTeX). `$x^2$` or the slash
 * menu's "Inline equation" makes one; a click edits its LaTeX. Exported as
 * `$…$`. */
export const inlineMath = createReactInlineContentSpec(
  { type: "inlineMath", propSchema: { latex: { default: "" } }, content: "none" },
  {
    render: ({ inlineContent, editor, getPos }) => {
      // Read, not cleared, here: StrictMode renders twice. The view clears it.
      const autoEdit = openNext && inlineContent.props.latex === "";
      const view = () => editor._tiptapEditor.view;
      return (
        <InlineMathView
          latex={inlineContent.props.latex}
          autoEdit={autoEdit}
          onMounted={() => {
            openNext = false;
          }}
          onCommit={(latex) => {
            const v = view();
            const pos = inlineMathAt(v, getPos);
            if (pos === null) return;
            const tr = latex
              ? v.state.tr.setNodeMarkup(pos, undefined, { latex })
              : v.state.tr.delete(pos, pos + 1);
            v.dispatch(tr);
          }}
          onLeave={(side) => {
            const v = view();
            const pos = inlineMathAt(v, getPos);
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
