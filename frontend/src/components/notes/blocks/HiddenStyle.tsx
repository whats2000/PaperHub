import { createReactStyleSpec } from "@blocknote/react";

/** `||text||`: hidden until clicked (a click again hides it). The look is in
 * index.css (`.note-hidden`); the transition there respects reduced motion. */
export const hiddenStyle = createReactStyleSpec(
  { type: "hidden", propSchema: "boolean" },
  {
    render: ({ contentRef }) => (
      <span
        ref={contentRef}
        className="note-hidden"
        onClick={(e) => {
          const el = e.currentTarget;
          if (el.hasAttribute("data-shown")) el.removeAttribute("data-shown");
          else el.setAttribute("data-shown", "");
        }}
      />
    ),
  },
);
