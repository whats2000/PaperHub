import { createReactBlockSpec } from "@blocknote/react";

import { markPropSchema, type MarkProps } from "./markProps";
import { PaperEquationView } from "./RefViews";

/** An equation marked on the paper: the model's LaTeX beside the page crop. */
export const paperEquationBlock = createReactBlockSpec(
  { type: "paperEquation", propSchema: markPropSchema, content: "none" },
  {
    render: ({ block }) => <PaperEquationView id={block.id} props={block.props as unknown as MarkProps} />,
  },
);
