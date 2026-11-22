import { createReactBlockSpec } from "@blocknote/react";

import { DividerView } from "./DividerView";

/** The note's divider: it splits a section into notes (it folds nothing). */
export const dividerBlock = createReactBlockSpec(
  { type: "divider", propSchema: {}, content: "none" },
  {
    render: ({ block, editor }) => <DividerView id={block.id} editor={editor as never} />,
    parse: (el) => (el.tagName === "HR" ? {} : undefined),
    toExternalHTML: () => <hr />,
  },
);
