import { createReactStyleSpec } from "@blocknote/react";

/** The anchor of a comment thread: the commented words carry the thread id
 * (the value). Neutral underline + tint, never yellow (citations own it); the
 * look is in index.css (`.note-comment`). Clicking it opens the thread (the
 * editor's click handler reads `data-comment-id`). Thread text is not here. */
export const commentStyle = createReactStyleSpec(
  { type: "comment", propSchema: "string" },
  {
    render: ({ contentRef, value }) => (
      <span ref={contentRef} className="note-comment" data-comment-id={value} />
    ),
  },
);
