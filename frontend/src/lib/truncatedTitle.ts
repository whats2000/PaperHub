import type { SyntheticEvent } from "react";

/** Handlers that give an element a hover / focus `title` with its full text
 * when the text is cut by its width (`scrollWidth > clientWidth`), and
 * `otherwise` (or no title) when it fits. The measured element is the one
 * marked `data-truncate` inside the target, else the target itself. */
export function truncatedTitle(full: string, otherwise = "") {
  const update = (e: SyntheticEvent<HTMLElement>) => {
    const el = e.currentTarget;
    const measured = el.querySelector<HTMLElement>("[data-truncate]") ?? el;
    const cut = measured.scrollWidth > measured.clientWidth;
    const title = cut ? full : otherwise;
    if (title) el.setAttribute("title", title);
    else el.removeAttribute("title");
  };
  return { onPointerEnter: update, onFocus: update };
}
