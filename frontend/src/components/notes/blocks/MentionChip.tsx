import { createReactInlineContentSpec } from "@blocknote/react";
import { AtSign } from "lucide-react";

import { useCanvasStore } from "@/store/canvas";

/** An `@` link to a paper or one of its sections; clicking opens the paper. */
export const mentionInline = createReactInlineContentSpec(
  {
    type: "mention",
    propSchema: {
      kind: { default: "paper", values: ["paper", "section"] as const },
      paperId: { default: 0 },
      sectionKey: { default: "" },
      label: { default: "" },
    },
    content: "none",
  },
  {
    render: ({ inlineContent }) => {
      const { paperId, label } = inlineContent.props;
      return (
        <button
          type="button"
          contentEditable={false}
          className="mx-px inline-flex items-center gap-0.5 rounded-md bg-muted px-1 align-baseline text-[0.95em] text-foreground underline decoration-muted-foreground/40 underline-offset-2 transition-colors hover:bg-accent"
          onClick={() => {
            if (paperId > 0) useCanvasStore.getState().openPaper(paperId);
          }}
        >
          <AtSign className="size-3 text-muted-foreground" aria-hidden />
          {label}
        </button>
      );
    },
  },
);
