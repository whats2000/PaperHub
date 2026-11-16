import { createStyleSpec } from "@blocknote/core";

/** The anchor of a comment thread: the commented words carry the thread id
 * (the value). Neutral underline + tint, never yellow (citations own it); the
 * look is in index.css (`.note-comment`). Clicking it opens the thread (the
 * editor's click handler reads `data-comment-id`). Thread text is not here.
 *
 * Plain DOM, not a React mark view (see `HiddenStyle`): a late React render
 * moved the caret. */
export const commentStyle = createStyleSpec(
  { type: "comment", propSchema: "string" },
  {
    render: (value) => {
      const dom = document.createElement("span");
      dom.className = "note-comment";
      dom.setAttribute("data-comment-id", value ?? "");
      return { dom, contentDOM: dom };
    },
  },
);
