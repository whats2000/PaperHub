import { createReactBlockSpec } from "@blocknote/react";

import { markPropSchema, type MarkProps } from "./markProps";
import { PaperFigureView } from "./RefViews";

/** A figure marked whole on the paper: its page crop and caption. */
export const paperFigureBlock = createReactBlockSpec(
  { type: "paperFigure", propSchema: markPropSchema, content: "none" },
  {
    render: ({ block }) => <PaperFigureView id={block.id} props={block.props as unknown as MarkProps} />,
  },
);
