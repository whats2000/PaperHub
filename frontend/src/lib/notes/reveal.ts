/** Bring a note block into view: scroll it to the middle and flash a short
 * neutral ring (`data-flash`, styled in index.css). Used by "Open in notes",
 * `[[` heading links and the table of contents. A block in a folded part is
 * unfolded first; a block not drawn yet (its note still loading) is waited
 * for, up to a limit. */
import { partOf } from "@/components/notes/parts";
import { prefersReducedMotion } from "@/lib/motion";
import { useNotesStore } from "@/store/notes";

/** How long a reveal waits for its block to show. */
export const REVEAL_GIVE_UP_MS = 5000;
/** How long the ring stays. */
export const FLASH_MS = 1600;

export interface RevealOptions {
  /** Called once when the block is in a folded part (`data-folded`). */
  unfold?: (blockId: string) => void;
  /** Stops waiting (the promise resolves false). */
  signal?: AbortSignal;
}

const blockSelector = (id: string) => `.bn-block-outer[data-id="${CSS.escape(id)}"]`;

/** Reveals block `blockId` inside `root`: true once shown, false when it
 * never showed (or `signal` aborted). */
export function revealBlock(root: HTMLElement, blockId: string, opts: RevealOptions = {}): Promise<boolean> {
  const { unfold, signal } = opts;
  if (signal?.aborted) return Promise.resolve(false);
  let unfolded = false;

  const tryReveal = (): boolean => {
    const el = root.querySelector<HTMLElement>(blockSelector(blockId));
    if (!el) return false;
    if (el.getAttribute("data-folded") === "true" && unfold) {
      // Wait for the part to open: a folded block has no place to scroll to.
      if (!unfolded) {
        unfolded = true;
        unfold(blockId);
      }
      return false;
    }
    el.scrollIntoView?.({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    el.setAttribute("data-flash", "");
    // Outlives the caller (a consumed request re-renders the pane).
    setTimeout(() => el.removeAttribute("data-flash"), FLASH_MS);
    return true;
  };

  if (tryReveal()) return Promise.resolve(true);
  return new Promise<boolean>((resolve) => {
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      observer.disconnect();
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve(ok);
    };
    const onAbort = () => finish(false);
    // The editor mounts after its note loads, and an unfold redraws the part.
    const observer = new MutationObserver(() => {
      if (!done && tryReveal()) finish(true);
    });
    observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-folded"] });
    const timer = setTimeout(() => finish(false), REVEAL_GIVE_UP_MS);
    signal?.addEventListener("abort", onAbort);
  });
}

/** An `unfold` for note `noteId`: opens the folded part holding a block. */
export function unfoldInNote(noteId: number): (blockId: string) => void {
  return (blockId) => {
    const store = useNotesStore.getState();
    const part = partOf(store.notes[noteId]?.blocks ?? [], blockId);
    if (part?.dividerId && (store.folded[noteId] ?? []).includes(part.dividerId)) {
      store.toggleFold(noteId, part.dividerId);
    }
  };
}
