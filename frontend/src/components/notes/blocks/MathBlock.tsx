import { createReactBlockSpec } from "@blocknote/react";

import { MathView } from "./MathView";

/** A display equation of the user's own (LaTeX). One just made (`$$` +
 * Enter or space, or the slash menu: empty, with the editor's caret on it)
 * opens with the caret in its LaTeX, so typing goes on there. Leaving its
 * LaTeX with a key puts the editor's caret below it (a new line when there is
 * none), above it, or on it (Escape). */
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
        onLeave={(to) => {
          editor.focus();
          if (to === "up") {
            const prev = editor.getPrevBlock(block);
            if (prev) editor.setTextCursorPosition(prev, "end");
            return;
          }
          if (to === "stay") {
            editor.setTextCursorPosition(block, "start");
            return;
          }
          const next = editor.getNextBlock(block);
          const line = next ?? editor.insertBlocks([{ type: "paragraph" } as never], block, "after")[0];
          if (line) editor.setTextCursorPosition(line, "start");
        }}
      />
    ),
  },
);
