import { createReactInlineContentSpec } from "@blocknote/react";
import { TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

import { NoteLinkView } from "./NoteLinkView";

function linkAt(view: EditorView, getPos: () => number | undefined): number | null {
  const pos = getPos();
  return typeof pos === "number" && view.state.doc.nodeAt(pos)?.type.name === "noteLink" ? pos : null;
}

/** A `[[` link to a note (one of its headings: `blockId`) or a library
 * paper (one of its sections: `sectionKey`). `label` is the user's display
 * text ("" = follow the target's current name); `name` is the target's name
 * when the link was made, shown until the current one is known and read by
 * search, export and the agent. */
export const noteLink = createReactInlineContentSpec(
  {
    type: "noteLink",
    propSchema: {
      targetKind: { default: "note", values: ["note", "paper"] as const },
      targetId: { default: 0 },
      blockId: { default: "" },
      sectionKey: { default: "" },
      label: { default: "" },
      name: { default: "" },
    },
    content: "none",
  },
  {
    render: ({ inlineContent, editor, getPos }) => {
      const view = () => editor._tiptapEditor.view;
      return (
        <NoteLinkView
          {...inlineContent.props}
          onCommitLabel={(label) => {
            const v = view();
            const pos = linkAt(v, getPos);
            if (pos === null) return;
            const attrs = v.state.doc.nodeAt(pos)!.attrs;
            v.dispatch(v.state.tr.setNodeMarkup(pos, undefined, { ...attrs, label }));
          }}
          onLeave={(side) => {
            const v = view();
            const pos = linkAt(v, getPos);
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
