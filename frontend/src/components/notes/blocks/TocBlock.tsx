import { createReactBlockSpec } from "@blocknote/react";

import { TocView, type TocEditor } from "./TocView";

export const tocBlock = createReactBlockSpec(
  { type: "toc", propSchema: {}, content: "none" },
  { render: ({ editor }) => <TocView editor={editor as unknown as TocEditor} /> },
);
