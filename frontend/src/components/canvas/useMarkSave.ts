/** Saving marks from a reader (Plan H5b.1), shared by the HTML and PDF
 * readers: one mark per section the selection crosses, one confirmation for
 * all of them, a retry that saves only what is still missing, and Add note's
 * caret after the new block. Highlighter strokes (`usePenSave`) save the
 * same way, one after another with erase and split (`queueMarkOp`). */
import type { TFunction } from "i18next";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import i18n from "@/lib/i18n";
import { joinTarget } from "@/lib/marks/pieces";
import { markHint, queueMarkOp, toastSaved, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkColor, MarkCreated, MarkIn, MarkKind, MarkPiece } from "@/types/notes";

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

/** What one selection (or stroke) has saved so far. Keyed by what is
 * selected and the action's kind and colour, not by the selection object:
 * re-reading the same selection makes a new one over the same text.
 * Another action starts fresh; the marks it already saved stay as they
 * are. */
interface Progress {
  key: string;
  saved: Map<string, MarkCreated>;
}

const groupKey = (g: MarkGroup) => JSON.stringify([g.section, g.pieces]);

/** The progress key of an action over these groups. */
const progressKey = (kind: MarkKind, color: MarkColor | null, view: string, groups: readonly MarkGroup[]) =>
  JSON.stringify([kind, color, view, groups.map(groupKey)]);

type Create = (body: MarkIn, opts?: { quiet?: boolean; silent?: boolean }) => Promise<MarkCreated>;

/** Saves each group not yet in `done` (one mark per section), in order;
 * throws at the first failure, keeping what was saved. `each` sees every
 * mark as it is saved. */
async function saveMissing(
  create: Create,
  body: Omit<MarkIn, "pieces" | "section_key">,
  groups: readonly MarkGroup[],
  done: Map<string, MarkCreated>,
  opts: { silent?: boolean; each?: (res: MarkCreated, index: number) => void; save?: (body: MarkIn) => Promise<MarkCreated> } = {},
): Promise<MarkCreated[]> {
  for (const [i, group] of groups.entries()) {
    const k = groupKey(group);
    if (done.has(k)) continue;
    const full: MarkIn = { ...body, pieces: group.pieces, section_key: group.section };
    const res = opts.save ? await opts.save(full) : await create(full, { quiet: true, silent: opts.silent });
    done.set(k, res);
    opts.each?.(res, i);
  }
  return groups.map((g) => done.get(groupKey(g))!);
}

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
        const key = progressKey(kind, color, view, groups);
        if (progress.current?.key !== key) progress.current = { key, saved: new Map() };
        // A retry after a failure saves only the sections still missing.
        const results = await saveMissing(
          create,
          { paper_content_id: paperId, kind, color, view, hint_block_id: markHint(paperId) },
          groups,
          progress.current.saved,
        );
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

/** A join the server refused: the mark it names was deleted meanwhile (or
 * no longer matches). The stroke is then a mark of its own. */
const refusedJoin = (err: unknown) => err instanceof ApiError && err.status === 422;

/** Saving highlighter strokes, one after another and in order with erase
 * and split (`queueMarkOp`): a stroke in one paragraph joins the pen
 * session's mark of the same colour there; a stroke across paragraphs is a
 * new mark per section, each piece filed under its own. Each mark joins the
 * session as soon as it is saved. A stroke that fails says so with Retry,
 * which saves only what is still missing (re-stroking the same text does
 * too). */
export function usePenSave(paperId: number | null): (stroke: PenStroke) => void {
  const create = useMarksStore((s) => s.create);
  // Per stroke (by what it marks): what a failed stroke saved so far, for a
  // re-stroke of the same text. It is forgotten when the pen session ends
  // or the paper changes; a Retry keeps its own record.
  const progress = useRef(new Map<string, Map<string, MarkCreated>>());
  useEffect(() => {
    const forget = progress.current;
    forget.clear();
    const unsubscribe = useMarksStore.subscribe((now, before) => {
      if (before.penOn && !now.penOn) forget.clear();
    });
    return () => {
      unsubscribe();
      forget.clear();
    };
  }, [paperId]);

  return useCallback(
    (stroke: PenStroke) => {
      // The colour the stroke was drawn in.
      const color = useMarksStore.getState().penColor;
      const t = i18n.t.bind(i18n) as TFunction;
      // What the stroke marks, once read, and what it has saved: a Retry
      // carries on from there.
      let placedOnce: PenPiece[] | undefined;
      let savedOnce: Map<string, MarkCreated> | undefined;

      const failed = () =>
        toast.error(t("notes:marks.saveFailed", "Couldn't save the mark."), {
          action: { label: t("notes:marks.retry", "Retry"), onClick: () => enqueue() },
        });

      const run = async (signal: AbortSignal): Promise<void> => {
        if (paperId === null) return stroke.done?.(false);
        const placed = placedOnce ?? (await stroke.pieces());
        if (!placed?.length) return stroke.done?.(false);
        placedOnce = placed;
        // A stroke drawn just before the pen went off still saves; it only
        // joins nothing (the session ended).
        const { penSession, marksByPaper, notePenMark } = useMarksStore.getState();
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
        const plain = groups.map(({ section, pieces }) => ({ section, pieces }));
        const key = progressKey(kind, color, stroke.view, plain);
        const done = savedOnce ?? progress.current.get(key) ?? new Map<string, MarkCreated>();
        savedOnce = done;
        progress.current.set(key, done);
        // Set only when this run itself joined a piece to a mark.
        let joinedNow: string | null = null;
        const opts = { quiet: true, silent: true, signal };
        try {
          const results = await saveMissing(
            create,
            { paper_content_id: paperId, kind, color, view: stroke.view, hint_block_id: markHint(paperId) },
            plain,
            done,
            {
              silent: true,
              save: async (body) => {
                if (!target) return create(body, opts);
                try {
                  const res = await create({ ...body, join_to: target }, opts);
                  joinedNow = target;
                  return res;
                } catch (err) {
                  if (!refusedJoin(err)) throw err;
                  return create(body, opts);
                }
              },
              // In the session as soon as it is saved: a stroke that fails
              // later still lets the next stroke join what did save.
              each: (res, i) => {
                if (kind !== "highlight") return;
                const prev = session.find((m) => m.id === res.mark.id);
                notePenMark({ id: res.mark.id, color, places: [...(prev?.places ?? []), ...groups[i]!.places] });
              },
            },
          );
          progress.current.delete(key);
          if (joinedNow) toastJoined(joinedNow);
          else toastSaved(paperId, results);
          stroke.done?.(true);
        } catch {
          // What saved stays saved; Retry saves the rest. A timeout already
          // said so (queueMarkOp).
          if (!signal.aborted) failed();
          stroke.done?.(false);
        }
      };
      const enqueue = () =>
        void queueMarkOp(run, () => {
          failed();
          stroke.done?.(false);
        }).catch(() => undefined);
      enqueue();
    },
    [paperId, create],
  );
}
