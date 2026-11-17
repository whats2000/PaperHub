/** Saving marks from a reader (Plan H5b.1), shared by the HTML and PDF
 * readers: one mark per section the selection crosses, one confirmation for
 * all of them, a retry that saves only what is still missing, and Add note's
 * caret after the new block. */
import { useCallback, useRef, useState } from "react";

import { markHint, toastSaved, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkColor, MarkCreated, MarkKind, MarkPiece } from "@/types/notes";

/** The pieces of one mark and the section it files under. */
export interface MarkGroup {
  section: string | null;
  pieces: MarkPiece[];
}

export interface MarkSaveRequest {
  kind: MarkKind;
  color: MarkColor | null;
  view: "html" | "pdf";
  /** Add note: the notes pane opens with the caret after the new block. */
  addNote: boolean;
  /** The marks to save (one per section), or null when they can't be had
   * (the reader has already said why). */
  groups: () => Promise<MarkGroup[] | null> | MarkGroup[] | null;
}

/** What one selection has saved so far. Keyed by what is selected and the
 * action's kind and colour, not by the selection object: re-reading the same
 * selection makes a new one over the same text. Another action starts
 * fresh; the marks it already saved stay as they are. */
interface Progress {
  key: string;
  saved: Map<string, MarkCreated>;
}

const groupKey = (g: MarkGroup) => JSON.stringify([g.section, g.pieces]);

/** Opens a mark's block in the notes pane (until the note card). */
export function openMarkInNotes(paperId: number, mark: Mark | undefined): void {
  if (!mark || mark.note_id === undefined) return;
  useNotesPaneStore.getState().openAt({ kind: "paper", id: paperId, noteId: mark.note_id, blockId: mark.block_id });
}

export function useMarkSave(paperId: number | null): {
  busy: boolean;
  /** True once every mark is saved (the selection can go). */
  save: (req: MarkSaveRequest) => Promise<boolean>;
} {
  const create = useMarksStore((s) => s.create);
  const progress = useRef<Progress | null>(null);
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);

  const save = useCallback(
    async ({ kind, color, view, addNote, groups: getGroups }: MarkSaveRequest): Promise<boolean> => {
      if (paperId === null || busyRef.current) return false;
      busyRef.current = true;
      setBusy(true);
      try {
        const groups = await getGroups();
        if (!groups?.length) return false;
        const key = JSON.stringify([kind, color, groups.map(groupKey)]);
        if (progress.current?.key !== key) progress.current = { key, saved: new Map() };
        const done = progress.current.saved;
        const hint = markHint(paperId);
        // A mark files under one section; a retry after a failure saves
        // only the sections still missing.
        for (const group of groups) {
          const k = groupKey(group);
          if (done.has(k)) continue;
          const res = await create(
            {
              paper_content_id: paperId,
              kind,
              color,
              view,
              pieces: group.pieces,
              section_key: group.section,
              hint_block_id: hint,
            },
            { quiet: true },
          );
          done.set(k, res);
        }
        const results = [...done.values()];
        progress.current = null;
        toastSaved(paperId, results);
        const last = results[results.length - 1];
        if (addNote && last) {
          const target = { noteId: last.saved_to.note_id, blockId: last.mark.block_id };
          useNotesStore.getState().setCaretAfter(target);
          useNotesPaneStore.getState().openAt({ kind: "paper", id: paperId, ...target });
        }
        return true;
      } catch {
        // The store said so; the selection stays to try again.
        return false;
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [paperId, create],
  );

  return { busy, save };
}
