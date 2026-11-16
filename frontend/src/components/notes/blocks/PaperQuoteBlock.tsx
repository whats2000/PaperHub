import { createReactBlockSpec } from "@blocknote/react";

import { markPropSchema, type MarkProps } from "./markProps";
import { PaperQuoteView } from "./RefViews";

/** A highlight or quote marked on the paper (non-editable content). */
export const paperQuoteBlock = createReactBlockSpec(
  { type: "paperQuote", propSchema: markPropSchema, content: "none" },
  {
    render: ({ block }) => <PaperQuoteView id={block.id} props={block.props as unknown as MarkProps} />,
  },
);
