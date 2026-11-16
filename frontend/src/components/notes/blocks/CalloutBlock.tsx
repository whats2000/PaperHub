import { createReactBlockSpec } from "@blocknote/react";
import { Lightbulb } from "lucide-react";

/** A Notion-style callout: a tinted box with an icon around inline text. */
export const calloutBlock = createReactBlockSpec(
  { type: "callout", propSchema: {}, content: "inline" },
  {
    render: ({ contentRef }) => (
      <div className="flex w-full items-start gap-2 rounded-md bg-muted px-3 py-2">
        <Lightbulb className="mt-1 size-4 shrink-0 text-muted-foreground" aria-hidden />
        <div ref={contentRef} className="min-w-0 flex-1" />
      </div>
    ),
  },
);
