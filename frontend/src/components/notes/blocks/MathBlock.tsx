import { createReactBlockSpec } from "@blocknote/react";

import { MathView } from "./MathView";

/** A display equation of the user's own (LaTeX). One just made (`$$ ` or the
 * slash menu: empty, with the editor's caret on it) opens with the caret in
 * its LaTeX, so typing goes on there. */
export const mathBlock = createReactBlockSpec(
  { type: "math", propSchema: { latex: { default: "" } }, content: "none" },
  {
    render: ({ block, editor }) => (
      <MathView
        latex={block.props.latex}
        isNew={() =>
          block.props.latex === "" && editor.isFocused() && editor.getTextCursorPosition().block.id === block.id
        }
        onChange={(latex) => editor.updateBlock(block, { props: { latex } })}
      />
    ),
  },
);
