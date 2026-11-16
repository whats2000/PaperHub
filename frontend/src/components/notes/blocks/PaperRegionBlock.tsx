import { createReactBlockSpec } from "@blocknote/react";

import { markPropSchema, type MarkProps } from "./markProps";
import { PaperRegionView } from "./RefViews";

/** A box drawn on a page: the text under it beside the page crop. */
export const paperRegionBlock = createReactBlockSpec(
  { type: "paperRegion", propSchema: markPropSchema, content: "none" },
  {
    render: ({ block }) => <PaperRegionView id={block.id} props={block.props as unknown as MarkProps} />,
  },
);
