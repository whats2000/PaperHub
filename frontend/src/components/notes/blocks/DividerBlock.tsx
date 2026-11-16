import { createReactBlockSpec } from "@blocknote/react";

import { DividerView } from "./DividerView";

/** The note's divider: it splits a section into notes and folds the one below. */
export const dividerBlock = createReactBlockSpec(
  { type: "divider", propSchema: {}, content: "none" },
  {
    render: ({ block }) => <DividerView id={block.id} />,
    parse: (el) => (el.tagName === "HR" ? {} : undefined),
    toExternalHTML: () => <hr />,
  },
);
