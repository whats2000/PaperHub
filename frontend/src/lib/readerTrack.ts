/** The reader's HTML/PDF track slides between the two views (a 200 ms
 * transform). While it slides, a mark on it moves sideways with it; a note
 * card pointing at the mark holds still until the slide ends
 * [user, 2026-11-19]. */
export function readerTrackSliding(): boolean {
  const el = document.querySelector("[data-reader-track]");
  if (!el || typeof el.getAnimations !== "function") return false;
  return el.getAnimations().some((a) => a.playState === "running");
}
