import { createReactBlockSpec } from "@blocknote/react";

/** A paper section's heading in its note: read-only, keyed by the section. */
export const sectionBlock = createReactBlockSpec(
  {
    type: "section",
    propSchema: { title: { default: "" }, sectionKey: { default: "" } },
    content: "none",
  },
  {
    render: ({ block }) => (
      <h3
        contentEditable={false}
        data-section-key={block.props.sectionKey}
        className="m-0 w-full pt-2 text-[15px] font-semibold tracking-tight text-foreground select-none"
      >
        {block.props.title}
      </h3>
    ),
  },
);
