import { createReactBlockSpec } from "@blocknote/react";

import { MathView } from "./MathView";

/** A display equation of the user's own (LaTeX). */
export const mathBlock = createReactBlockSpec(
  { type: "math", propSchema: { latex: { default: "" } }, content: "none" },
  {
    render: ({ block, editor }) => (
      <MathView
        latex={block.props.latex}
        onChange={(latex) => editor.updateBlock(block, { props: { latex } })}
      />
    ),
  },
);
