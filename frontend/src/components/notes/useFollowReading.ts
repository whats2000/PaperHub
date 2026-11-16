/** Follow: the note scrolls to the section the reader is at.
 *
 * While Follow is on, each new section the reader reports for this note's
 * paper scrolls the note's section heading to the top and makes its first
 * part the active one (unless Focus is on: Focus moves only on a click).
 * With Follow off the note stays where the user left it; turning Follow back
 * on catches up with the reader.
 */
import { useEffect, useMemo } from "react";

import { prefersReducedMotion } from "@/lib/motion";
import { useCanvasStore } from "@/store/canvas";
import { useNotesStore } from "@/store/notes";

import { sectionLabel } from "./blocks/markProps";
import { sectionForHeading } from "./parts";

export interface FollowReadingArgs {
  noteId: number | null;
  /** The paper of the note (only a paper note follows the reader). */
  paperId: number | null;
  /** The element holding the note's editor. */
  root: HTMLElement | null;
}

/** Returns the label of the section followed ("§2"), or null. */
export function useFollowReading({ noteId, paperId, root }: FollowReadingArgs): string | null {
  const followOn = useNotesStore((s) => s.followOn);
  const blocks = useNotesStore((s) => (noteId === null ? undefined : s.notes[noteId]?.blocks));
  const reading = useCanvasStore((s) =>
    paperId !== null && s.readingSection?.paperId === paperId ? s.readingSection : null,
  );

  const section = useMemo(
    () => (blocks && reading ? sectionForHeading(blocks, reading.heading, reading.within) : null),
    [blocks, reading],
  );
  const sectionId = section?.id ?? null;

  useEffect(() => {
    if (!followOn || noteId === null || sectionId === null || !root) return;
    const el = root.querySelector(`.bn-block-outer[data-id="${CSS.escape(sectionId)}"]`);
    el?.scrollIntoView({ block: "start", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    const store = useNotesStore.getState();
    if (!store.focusOn && store.activePart[noteId] !== sectionId) store.setActivePart(noteId, sectionId);
    // Keyed by the section id: an edit to the note never moves it.
  }, [followOn, noteId, sectionId, root]);

  return section ? sectionLabel(section.section_key ?? "") : null;
}
