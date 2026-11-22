/** Bring a note block into view: scroll it to the middle and flash a short
 * neutral ring (a stylesheet rule keyed by the block id). Used by "Open in notes",
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
    flash(root, blockId, el);
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
function flash(root: HTMLElement, blockId: string, el: HTMLElement): void {
  const scroll = (node: HTMLElement) =>
    node.scrollIntoView?.({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  scroll(el);
  rings.set(blockId, (rings.get(blockId) ?? 0) + 1);
  writeRings();
  // The note may reload right after (a new mark): the block is drawn anew,
  // at once or a moment later; it is scrolled to again and rings for its
  // full time then.
  let shown: HTMLElement = el;
  const follow = new MutationObserver(() => {
    if (shown.isConnected) return;
    const now = root.querySelector<HTMLElement>(blockSelector(blockId));
    if (!now) return;
    shown = now;
    scroll(now);
    clearTimeout(timer);
    timer = setTimeout(end, FLASH_MS);
  });
  follow.observe(root, { childList: true, subtree: true });
  const end = () => {
    follow.disconnect();
    clearTimeout(cap);
    const n = (rings.get(blockId) ?? 1) - 1;
    if (n > 0) rings.set(blockId, n);
    else rings.delete(blockId);
    writeRings();
  };
  let timer = setTimeout(end, FLASH_MS);
  // Never left ringing: a block that never comes back.
  const cap = setTimeout(() => {
    clearTimeout(timer);
    end();
  }, REVEAL_GIVE_UP_MS);
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
  // The neutral ring of index.css's `[data-flash]` (yellow is for citations),
  // on a plain block or a React one (a mark's reference block).
  el.textContent = [...rings.keys()]
    .map((id) => {
      const block = `.paperhub-notes .bn-block-outer[data-id="${CSS.escape(id)}"] > .bn-block`;
      return (
        `${block} > .bn-block-content, ${block} > .react-renderer > .bn-block-content` +
        " { border-radius: 4px; box-shadow: 0 0 0 2px color-mix(in srgb, var(--foreground) 35%, transparent); transition: box-shadow 0s; }"
      );
    })
    .join(" ");
}

/** Whether block `blockId` is ringed now (tests). */
export function isRinged(blockId: string): boolean {
  return rings.has(blockId);
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
