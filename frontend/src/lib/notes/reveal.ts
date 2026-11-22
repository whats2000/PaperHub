/** Bring a note block into view: scroll it to the middle and flash a short
 * neutral ring (a stylesheet rule keyed by the block id). Used by "Open in notes",
 * `[[` heading links and the table of contents. A block under a fold (a
 * folded heading, § section or list item) is unfolded first; a block not
 * drawn yet (its note still loading) is waited for, up to a limit. */
import { foldersHiding, treeFromRows } from "@/components/notes/folds";
import { prefersReducedMotion } from "@/lib/motion";
import { useNotesStore } from "@/store/notes";

/** How long a reveal waits for its block to show. */
export const REVEAL_GIVE_UP_MS = 5000;
/** How long the ring stays. */
export const FLASH_MS = 1600;

export interface RevealOptions {
  /** Called once when the block is under a fold (it, or a block it is
   * nested in, is `data-folded`). */
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
    if (el.closest('[data-folded="true"]') && unfold) {
      // Wait for the fold to open: a folded block has no place to scroll to.
      if (!unfolded) {
        unfolded = true;
        unfold(blockId);
      }
      return false;
    }
    flash(blockId, el);
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

/** Scrolls `el` to the middle and rings its block for FLASH_MS. The ring
 * is a stylesheet rule keyed by the block id, never an attribute on the
 * editor's DOM: ProseMirror redraws a block whose attributes change, which
 * wiped the ring at once (live bug, 2026-11-22). Outlives the caller (a
 * consumed request re-renders the pane). */
function flash(blockId: string, el: HTMLElement): void {
  el.scrollIntoView?.({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  rings.set(blockId, (rings.get(blockId) ?? 0) + 1);
  writeRings();
  setTimeout(() => {
    const n = (rings.get(blockId) ?? 1) - 1;
    if (n > 0) rings.set(blockId, n);
    else rings.delete(blockId);
    writeRings();
  }, FLASH_MS);
}

/** Blocks ringing now (a count: the same block revealed twice). */
const rings = new Map<string, number>();

function writeRings(): void {
  if (typeof document === "undefined") return;
  let el = document.getElementById("ph-note-rings");
  if (!el) {
    el = document.createElement("style");
    el.id = "ph-note-rings";
    document.head.appendChild(el);
  }
  // The neutral ring of index.css's `[data-flash]` (yellow is for citations).
  el.textContent = [...rings.keys()]
    .map(
      (id) =>
        `.paperhub-notes .bn-block-outer[data-id="${CSS.escape(id)}"] > .bn-block > .bn-block-content` +
        " { border-radius: 4px; box-shadow: 0 0 0 2px color-mix(in srgb, var(--foreground) 35%, transparent); transition: box-shadow 0s; }",
    )
    .join(" ");
}

/** Whether block `blockId` is ringed now (tests). */
export function isRinged(blockId: string): boolean {
  return rings.has(blockId);
}

/** An `unfold` for note `noteId`: opens every fold hiding a block. */
export function unfoldInNote(noteId: number): (blockId: string) => void {
  return (blockId) => {
    const store = useNotesStore.getState();
    const folded = store.folded[noteId] ?? [];
    const hiding = foldersHiding(treeFromRows(store.notes[noteId]?.blocks ?? []), blockId, new Set(folded));
    if (hiding.length) store.setFolded(noteId, folded.filter((id) => !hiding.includes(id)));
  };
}
