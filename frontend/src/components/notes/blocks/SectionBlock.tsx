import { createReactBlockSpec } from "@blocknote/react";

import { SectionFoldChip } from "../FoldControls";

/** A paper section's heading in its note: read-only, keyed by the section. */
export const sectionBlock = createReactBlockSpec(
  {
    type: "section",
    propSchema: { title: { default: "" }, sectionKey: { default: "" } },
    content: "none",
  },
  {
    render: ({ block, editor }) => (
      <h3
        contentEditable={false}
        data-section-key={block.props.sectionKey}
        className="m-0 w-full pt-2 text-[15px] font-semibold tracking-tight text-foreground select-none"
      >
        {block.props.title}
        <SectionFoldChip id={block.id} editor={editor as never} />
      </h3>
    ),
  },
);
