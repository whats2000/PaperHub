/** Saving marks from a reader (Plan H5b.1), shared by the HTML and PDF
 * readers: one mark per section the selection crosses, one confirmation for
 * all of them, a retry that saves only what is still missing, and Add note's
 * caret after the new block. */
import type { TFunction } from "i18next";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import i18n from "@/lib/i18n";
import { joinTarget } from "@/lib/marks/pieces";
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
  /** A highlighter stroke joining a mark of this pen session: the one group
   * is added to that mark as more pieces. */
  joinTo?: string | null;
  /** In place of the "Saved to …" confirmation. */
  confirm?: (results: MarkCreated[]) => void;
  /** Every mark saved, with the group each came from (in order). */
  onSaved?: (results: MarkCreated[], groups: MarkGroup[]) => void;
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
    async ({
      kind,
      color,
      view,
      addNote,
      groups: getGroups,
      joinTo = null,
      confirm,
      onSaved,
    }: MarkSaveRequest): Promise<boolean> => {
      if (paperId === null || busyRef.current) return false;
      busyRef.current = true;
      setBusy(true);
      try {
        const groups = await getGroups();
        if (!groups?.length) return false;
        const key = JSON.stringify([kind, color, joinTo, groups.map(groupKey)]);
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
              ...(joinTo ? { join_to: joinTo } : {}),
            },
            { quiet: true },
          );
          done.set(k, res);
        }
        const results = groups.map((g) => done.get(groupKey(g))!);
        progress.current = null;
        if (confirm) confirm(results);
        else toastSaved(paperId, results);
        onSaved?.(results, groups);
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

/** One highlighter stroke's piece: where it files and the paragraph (HTML)
 * or Marker block (PDF) it sits in (null: unknown, never joins). */
export interface PenPiece {
  section: string | null;
  piece: MarkPiece;
  place: string | null;
  /** What it turned out to be, once read (a box over an equation). */
  kind?: MarkKind;
}

export interface PenStroke {
  view: "html" | "pdf";
  /** The stroke's pieces, one per paragraph (null: can't be had, already
   * said why). Read once the stroke's turn comes. */
  pieces: () => Promise<PenPiece[] | null> | PenPiece[] | null;
  /** A figure or equation taken whole (never joins). */
  kind?: MarkKind;
  /** After the save (true: saved). */
  done?: (ok: boolean) => void;
}

/** "Joined to the same mark", with Split: the newest piece its own mark. */
function toastJoined(markId: string): void {
  const t = i18n.t.bind(i18n) as TFunction;
  toast(t("notes:marks.joined", "Joined to the same mark"), {
    action: { label: t("notes:marks.split", "Split"), onClick: () => void useMarksStore.getState().split(markId) },
  });
}

/** Saving highlighter strokes, one after another (a fast second stroke
 * waits for the first): a stroke in one paragraph joins the pen session's
 * mark of the same colour there; a stroke across paragraphs is a new mark
 * per section, each piece filed under its own. */
export function usePenSave(paperId: number | null): (stroke: PenStroke) => void {
  const { save } = useMarkSave(paperId);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  return useCallback(
    (stroke: PenStroke) => {
      // The colour the stroke was drawn in.
      const color = useMarksStore.getState().penColor;
      const run = async () => {
        // A stroke drawn just before the pen went off still saves; it only
        // joins nothing (the session ended).
        const { penSession, marksByPaper, notePenMark } = useMarksStore.getState();
        if (paperId === null) return stroke.done?.(false);
        const placed = await stroke.pieces();
        if (!placed?.length) return stroke.done?.(false);
        const liveIds = new Set((marksByPaper[paperId] ?? []).filter((m) => m.deleted_at === null).map((m) => m.id));
        const session = penSession.filter((m) => liveIds.has(m.id));
        const kind = placed[0]!.kind ?? stroke.kind ?? "highlight";
        const one = placed.length === 1 ? placed[0]! : null;
        const target = kind === "highlight" && one ? joinTarget(session, { color, place: one.place }) : null;
        // Consecutive pieces under one section make one mark.
        const groups: (MarkGroup & { places: string[] })[] = [];
        for (const p of placed) {
          const last = groups[groups.length - 1];
          if (last && last.section === p.section) {
            last.pieces.push(p.piece);
            if (p.place) last.places.push(p.place);
          } else groups.push({ section: p.section, pieces: [p.piece], places: p.place ? [p.place] : [] });
        }
        const ok = await save({
          kind,
          color,
          view: stroke.view,
          addNote: false,
          joinTo: target,
          groups: () => groups.map(({ section, pieces }) => ({ section, pieces })),
          confirm: target ? () => toastJoined(target) : undefined,
          onSaved: (results) => {
            if (kind !== "highlight") return;
            results.forEach((r, i) => {
              const prev = session.find((m) => m.id === r.mark.id);
              const places = [...(prev?.places ?? []), ...groups[i]!.places];
              notePenMark({ id: r.mark.id, color, places });
            });
          },
        });
        stroke.done?.(ok);
      };
      chain.current = chain.current.then(run).catch(() => stroke.done?.(false));
    },
    [paperId, save],
  );
}
