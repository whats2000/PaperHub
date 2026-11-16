import { createStyleSpec } from "@blocknote/core";

/** `||text||`: hidden until clicked (a click again hides it). The look is in
 * index.css (`.note-hidden`); the transition there respects reduced motion.
 * The shown state is a transient peek on the DOM element only: it is never
 * saved, and re-rendering the note covers the text again.
 *
 * Drawn with plain DOM, not a React mark view: React fills a mark view after
 * the input rule has placed the caret, and the browser's caret, lost in the
 * swapped nodes, then jumped to the start of the line. */
export const hiddenStyle = createStyleSpec(
  { type: "hidden", propSchema: "boolean" },
  {
    render: () => {
      const dom = document.createElement("span");
      dom.className = "note-hidden";
      dom.addEventListener("click", () => {
        if (dom.hasAttribute("data-shown")) dom.removeAttribute("data-shown");
        else dom.setAttribute("data-shown", "");
      });
      // The peek is the view's own state: the editor must not re-read it.
      const ignoreMutation = (m: MutationRecord | { type: "selection" }) =>
        m.type === "attributes" && (m as MutationRecord).attributeName === "data-shown";
      return { dom, contentDOM: dom, ignoreMutation } as { dom: HTMLElement; contentDOM: HTMLElement };
    },
  },
);
