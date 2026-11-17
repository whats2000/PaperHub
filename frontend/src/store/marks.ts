/** Reading marks (Plan H5b.1): each paper's marks, the colours and their
 * names, and "Show my marks". A new mark lands as a block in the paper's
 * note on the server; an open notes editor is refreshed from there. */
import type { TFunction } from "i18next";
import { useMemo } from "react";
import { toast } from "sonner";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import { partsOfNote } from "@/components/notes/parts";
import { sectionLabel } from "@/components/notes/blocks/markProps";
import {
  createMark,
  deleteMark,
  erasePiece,
  getChunkAnchors,
  listMarkColors,
  listMarks,
  patchMarkColor,
  restoreMark,
  restorePiece,
  splitMark,
} from "@/lib/api";
import i18n from "@/lib/i18n";
import { chunkMap, type ChunkMap } from "@/lib/marks/anchor";
import { livePieceIndexes, type PenMark } from "@/lib/marks/pieces";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type {
  Mark,
  MarkColor,
  MarkColorInfo,
  MarkColorPatch,
  MarkCreated,
  MarkIn,
  SavedTo,
  SplitResult,
} from "@/types/notes";

const UNDO_MS = 5000;

interface MarksState {
  marksByPaper: Record<number, Mark[]>;
  /** Each paper's chunk spans, once loaded (absent: not loaded, or the
   * request failed — `ensureChunks` tries again). */
  chunksByPaper: Record<number, ChunkMap>;
  colors: MarkColorInfo[];
  /** "Show my marks": off hides every mark on the paper (kept, not deleted). */
  showMarks: boolean;
  /** What a jump to a mark showed for the moment (a hidden colour, Show my
   * marks off): each later jump there offers to hide it again, until it is
   * hidden again or made visible for good. */
  shownForNow: { colors: MarkColor[]; marks: boolean };
  setShowMarks: (on: boolean) => void;
  /** The Box tool (PDF pages): a drag draws a box instead of selecting
   * text. Turning it on turns the highlighter off. */
  boxOn: boolean;
  setBoxOn: (on: boolean) => void;
  /** The highlighter: every drag marks at once in `penColor` (the colour
   * chosen last, remembered). Turning it on turns Box off. */
  penOn: boolean;
  penColor: MarkColor;
  /** The marks the pen made since it was turned on: a stroke joins one of
   * them. Turning the pen off ends the session. */
  penSession: PenMark[];
  setPen: (on: boolean, color?: MarkColor) => void;
  notePenMark: (mark: PenMark) => void;
  /** Loads a paper's marks and chunk spans. */
  loadPaper: (paperId: number) => Promise<void>;
  /** The paper's marks again, as the server has them (the reconcile after a
   * timed-out op): merged so a mark created while the request was out
   * stays, and dropped when aborted or overtaken by a newer refetch. Marks
   * only: the chunk anchors are not fetched. */
  refetchMarks: (paperId: number, signal?: AbortSignal) => Promise<void>;
  loadColors: () => Promise<void>;
  /** Renames a colour or shows / hides its marks (shown at once, kept on
   * the server; put back with a toast when the server refuses). */
  patchColor: (color: MarkColor, patch: MarkColorPatch) => Promise<void>;
  /** The paper's chunk spans, loading them (again) when they aren't here
   * yet. Throws when they can't be had. */
  ensureChunks: (paperId: number) => Promise<ChunkMap>;
  /** Saves a mark (its block lands in the paper's note) and, unless
   * `quiet`, confirms where (`toastSaved`); unless `silent`, a failure is
   * said in a toast. */
  create: (body: MarkIn, opts?: { quiet?: boolean; silent?: boolean; signal?: AbortSignal }) => Promise<MarkCreated>;
  /** Deletes a mark and its block, with Undo (then Delete again). */
  remove: (markId: string) => Promise<void>;
  /** Erases one piece of a mark and keeps the rest, with Undo (then Erase
   * again); when the server finds the mark empty it is deleted (Undo, then
   * Delete again). Waits for pending strokes (`queueMarkOp`). */
  erase: (markId: string, index: number) => Promise<void>;
  /** The newest piece of a joined mark becomes its own mark. */
  split: (markId: string) => Promise<void>;
}

/** localStorage that never throws (private windows, blocked site data). */
const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      localStorage.setItem(name, value);
    } catch {
      /* ignore */
    }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name);
    } catch {
      /* ignore */
    }
  },
};

/** "Saved to <paper> › §2 · note 2 of 3": where a new mark went. */
export function savedToText(saved: SavedTo, t: TFunction): string {
  const paper = saved.paper_title;
  if (!saved.section) return t("notes:marks.savedToPaper", { defaultValue: "Saved to {{paper}}", paper });
  const section = sectionLabel(saved.section);
  if (saved.part_count > 1) {
    return t("notes:marks.savedToPart", {
      defaultValue: "Saved to {{paper}} › {{section}} · note {{index}} of {{count}}",
      paper,
      section,
      index: saved.part_index + 1,
      count: saved.part_count,
    });
  }
  return t("notes:marks.savedTo", { defaultValue: "Saved to {{paper}} › {{section}}", paper, section });
}

/** "Saved to <paper> › §2, §3": where one selection's marks went (one per
 * section it crossed). */
export function savedToManyText(saved: readonly SavedTo[], t: TFunction): string {
  if (saved.length === 1) return savedToText(saved[0]!, t);
  const sections = saved.map((s) => (s.section ? sectionLabel(s.section) : null)).filter((s): s is string => !!s);
  return t("notes:marks.savedToSections", {
    defaultValue: "Saved to {{paper}} › {{sections}}",
    paper: saved[0]?.paper_title ?? "",
    sections: sections.join(", "),
  });
}

/** One confirmation for one selection's marks, with Show (the first). */
export function toastSaved(paperId: number, results: readonly MarkCreated[]): void {
  const first = results[0];
  if (!first) return;
  const t = i18n.t.bind(i18n) as TFunction;
  toast(savedToManyText(results.map((r) => r.saved_to), t), {
    action: {
      label: t("notes:marks.show", "Show"),
      onClick: () =>
        useNotesPaneStore.getState().openAt({
          kind: "paper",
          id: paperId,
          noteId: first.saved_to.note_id,
          blockId: first.mark.block_id,
        }),
    },
  });
}

/** Marks the server re-judged (a paragraph translated again): the paper's
 * marks and their open notes as the server now has them. */
export function marksChanged(paperId: number, markIds: readonly string[]): void {
  void useMarksStore
    .getState()
    .refetchMarks(paperId)
    .then(() => {
      const list = useMarksStore.getState().marksByPaper[paperId] ?? [];
      const notes = new Set(list.filter((m) => markIds.includes(m.id)).map((m) => m.note_id));
      notes.forEach((id) => refreshNote(id));
    })
    .catch(() => undefined);
}

/** Shows a note's new state in an open editor (the server changed it). */
function refreshNote(noteId: number | undefined): void {
  if (noteId === undefined) return;
  const notes = useNotesStore.getState();
  if (notes.notes[noteId]) void notes.loadNote(noteId).catch(() => undefined);
}

/** Where in the paper's note a new mark should go: the block "Quote from
 * the paper" marked, else the end of the note the caret is in (a part a
 * divider starts). Null lets the server put it at the end of its section. */
export function markHint(paperId: number): string | null {
  const n = useNotesStore.getState();
  const noteId = n.byTarget[`paper:${paperId}`];
  if (noteId === undefined) return null;
  if (n.quoteTarget?.noteId === noteId) return n.quoteTarget.blockId;
  const active = n.activePart[noteId];
  const blocks = n.notes[noteId]?.blocks;
  // A section heading is also where Follow points; only a divider says
  // which note of the section the user is in.
  if (!active || !blocks || blocks.find((b) => b.id === active)?.type !== "divider") return null;
  const part = partsOfNote(blocks).find((p) => p.id === active);
  return part?.blocks[part.blocks.length - 1]?.id ?? null;
}

/** Focus: the marks quoted in the focused note of this paper's note (null
 * when Focus is off), so the others fade on the paper. */
export function useMarkFocusIds(paperId: number | null): ReadonlySet<string> | null {
  const noteId = useNotesStore((s) => (paperId === null ? undefined : s.byTarget[`paper:${paperId}`]));
  const focusOn = useNotesStore((s) => s.focusOn);
  const active = useNotesStore((s) => (noteId === undefined ? null : (s.activePart[noteId] ?? null)));
  const blocks = useNotesStore((s) => (noteId === undefined ? undefined : s.notes[noteId]?.blocks));
  return useMemo(() => {
    if (!focusOn || active === null || !blocks) return null;
    const part = partsOfNote(blocks).find((p) => p.id === active);
    if (!part) return null;
    return new Set(
      part.blocks.map((b) => b.props?.markId).filter((m): m is string => typeof m === "string" && m !== ""),
    );
  }, [focusOn, active, blocks]);
}

/** Mark operations that must not overtake each other: highlighter strokes
 * (a join), erase, restore, split and delete run one after another, in the
 * order the user made them. */
let markOps: Promise<unknown> = Promise.resolve();

/** How long one queued operation may take before it is given up. */
export const MARK_OP_TIMEOUT_MS = 20_000;
let opTimeoutMs = MARK_OP_TIMEOUT_MS;

/** A queued mark operation that took too long (it was aborted). */
export class MarkOpTimeout extends Error {
  constructor() {
    super("mark operation timed out");
  }
}

export interface MarkOpOptions {
  /** Says the op timed out (its own error toast). */
  onTimeout?: () => void;
  /** The paper the op changes: after a timeout its marks are fetched again. */
  paperId?: number | null;
}

/** Runs `op` within `ms`, else aborts it and fails with `MarkOpTimeout`. */
function within<T>(ms: number, op: (signal: AbortSignal) => Promise<T>, onTimeout?: () => void): Promise<T> {
  const ctl = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      ctl.abort();
      onTimeout?.();
      reject(new MarkOpTimeout());
    }, ms);
  });
  return Promise.race([op(ctl.signal), late]).finally(() => clearTimeout(timer));
}

/** After a timeout: the paper's marks as the server has them. The request
 * may have landed there (the answer just came too late), so the client's
 * copy can't be trusted; the next queued op sees the refetched marks. */
async function reconcile(paperId: number): Promise<void> {
  await within(opTimeoutMs, (signal) => useMarksStore.getState().refetchMarks(paperId, signal)).catch(() => undefined);
}

/** Runs `op` after every operation queued before it. Each gets at most
 * `MARK_OP_TIMEOUT_MS`: then its requests are aborted (`signal`), `onTimeout`
 * says so (the op's own error toast), it fails with `MarkOpTimeout`, the
 * paper's marks are fetched again (`paperId`), and only then does the queue
 * carry on, so one hung request never blocks later strokes, erases, splits
 * or deletes, and a Retry sees what actually landed. An op stays quiet about
 * a failure once its signal has aborted (the timeout said it). */
export function queueMarkOp<T>(op: (signal: AbortSignal) => Promise<T>, opts: MarkOpOptions = {}): Promise<T> {
  const run = markOps.then(() => within(opTimeoutMs, op, opts.onTimeout));
  markOps = run.catch((err: unknown) =>
    err instanceof MarkOpTimeout && typeof opts.paperId === "number" ? reconcile(opts.paperId) : undefined,
  );
  return run;
}

/** Test isolation: forget pending mark operations (and set the timeout). */
export function resetMarkOps(timeoutMs = MARK_OP_TIMEOUT_MS): void {
  markOps = Promise.resolve();
  opTimeoutMs = timeoutMs;
}

let colorsRequest: Promise<void> | null = null;
/** Per paper, the latest refetch: an older one's answer is dropped. */
const refetchGen = new Map<number, number>();
/** A sequence of local mark changes that don't wait in the queue (a create,
 * a join), and each mark's last one, so a refetch sent before the change
 * neither drops nor overwrites it. Kept only while a refetch is out: the
 * `since` of each refetch in flight. */
let changeSeq = 0;
const changedAt = new Map<string, number>();
const refetchesOut: number[] = [];

function noteLocalChange(markId: string): void {
  changeSeq += 1;
  if (refetchesOut.length > 0) changedAt.set(markId, changeSeq);
}

/** Forgets the changes no refetch in flight was sent before. */
function pruneLocalChanges(): void {
  if (refetchesOut.length === 0) {
    changedAt.clear();
    return;
  }
  const oldest = Math.min(...refetchesOut);
  for (const [id, seq] of changedAt) if (seq <= oldest) changedAt.delete(id);
}

/** Test hook: how many local changes are remembered. */
export function trackedMarkChanges(): number {
  return changedAt.size;
}
const chunkRequests = new Map<number, Promise<ChunkMap>>();

export const useMarksStore = create<MarksState>()(
  persist(
    (set, get) => {
      const t = i18n.t.bind(i18n) as TFunction;
      const put = (paperId: number, fn: (list: Mark[]) => Mark[]) =>
        set((s) => ({ marksByPaper: { ...s.marksByPaper, [paperId]: fn(s.marksByPaper[paperId] ?? []) } }));

      /** Deletes a mark, after any pending stroke has saved (a join to it
       * lands first, so it can't bring the mark back). */
      const removeNow = async (markId: string, signal: AbortSignal): Promise<void> => {
        const entry = Object.entries(get().marksByPaper)
          .map(([pid, list]) => [Number(pid), list.find((m) => m.id === markId)] as const)
          .find(([, m]) => m !== undefined);
        if (!entry) return;
        const [paperId, mark] = entry as [number, Mark];
        put(paperId, (list) => list.filter((m) => m.id !== markId));
        try {
          await deleteMark(markId, signal);
        } catch {
          // Timed out: the delete may have landed; the refetch decides. Only
          // a definite failure puts the mark back.
          if (signal.aborted) return;
          put(paperId, (list) => (list.some((m) => m.id === markId) ? list : [...list, mark]));
          toast.error(t("notes:marks.deleteFailed", "Couldn't delete the mark."));
          return;
        }
        refreshNote(mark.note_id);
        let undone = false;
        toast(t("notes:marks.deleted", "Mark deleted"), {
          duration: UNDO_MS,
          action: {
            label: t("notes:marks.undo", "Undo"),
            onClick: () => {
              if (undone) return;
              undone = true;
              const failed = () => toast.error(t("notes:marks.restoreFailed", "Couldn't restore the mark."));
              void queueMarkOp(async (signal) => {
                try {
                  await restoreMark(markId, signal);
                } catch {
                  if (!signal.aborted) failed();
                  return;
                }
                put(paperId, (list) => (list.some((m) => m.id === markId) ? list : [...list, mark]));
                refreshNote(mark.note_id);
                toast(t("notes:marks.restored", "Mark restored"), {
                  duration: UNDO_MS,
                  action: { label: t("notes:marks.deleteAgain", "Delete again"), onClick: () => void remove(markId) },
                });
              }, { onTimeout: failed, paperId }).catch(() => undefined);
            },
          },
        });
      };

      const remove = (markId: string): Promise<void> =>
        queueMarkOp((signal) => removeNow(markId, signal), {
          onTimeout: () => toast.error(t("notes:marks.deleteFailed", "Couldn't delete the mark.")),
          paperId: paperOf(markId),
        }).catch(() => undefined);

      /** The paper a mark is on (null: not in the store). */
      const paperOf = (markId: string): number | null => {
        for (const [pid, list] of Object.entries(get().marksByPaper)) {
          if (list.some((m) => m.id === markId)) return Number(pid);
        }
        return null;
      };

      /** The paper and mark of an id. */
      const find = (markId: string): [number, Mark] | null => {
        for (const [pid, list] of Object.entries(get().marksByPaper)) {
          const m = list.find((x) => x.id === markId);
          if (m) return [Number(pid), m];
        }
        return null;
      };
      /** A mark as the server returned it, keeping what only the list has. */
      const replace = (paperId: number, prev: Mark, next: Mark) =>
        put(paperId, (list) =>
          list.map((m) => (m.id === prev.id ? { ...next, note_id: prev.note_id, part_label: prev.part_label } : m)),
        );

      /** A mark the server has put back (or changed): in the list again. */
      const upsert = (paperId: number, prev: Mark, next: Mark) =>
        put(paperId, (list) => {
          const merged = { ...next, note_id: prev.note_id, part_label: prev.part_label };
          return list.some((m) => m.id === next.id)
            ? list.map((m) => (m.id === next.id ? merged : m))
            : [...list, merged];
        });

      /** Erases one piece, after any pending stroke has saved. The server
       * decides whether that leaves the mark empty (then deleted). */
      const eraseNow = async (markId: string, index: number, signal: AbortSignal): Promise<void> => {
        const entry = find(markId);
        if (!entry) return;
        const [paperId, mark] = entry;
        if (!livePieceIndexes(mark.pieces).includes(index)) return;
        let res: Mark;
        try {
          res = await erasePiece(markId, index, signal);
        } catch {
          if (!signal.aborted) toast.error(t("notes:marks.eraseFailed", "Couldn't erase that piece."));
          return;
        }
        const deleted = res.deleted_at !== null;
        if (deleted) put(paperId, (list) => list.filter((m) => m.id !== markId));
        else replace(paperId, mark, res);
        refreshNote(mark.note_id);
        let undone = false;
        toast(deleted ? t("notes:marks.deleted", "Mark deleted") : t("notes:marks.erased", "Piece erased"), {
          duration: UNDO_MS,
          action: {
            label: t("notes:marks.undo", "Undo"),
            onClick: () => {
              if (undone) return;
              undone = true;
              const failed = () => toast.error(t("notes:marks.restoreFailed", "Couldn't restore the mark."));
              void queueMarkOp(async (signal) => {
                let back: Mark;
                try {
                  back = await restorePiece(markId, index, signal);
                } catch {
                  if (!signal.aborted) failed();
                  return;
                }
                if (back.deleted_at === null) upsert(paperId, find(markId)?.[1] ?? mark, back);
                refreshNote(mark.note_id);
                toast(deleted ? t("notes:marks.restored", "Mark restored") : t("notes:marks.pieceRestored", "Piece restored"), {
                  duration: UNDO_MS,
                  action: {
                    label: deleted ? t("notes:marks.deleteAgain", "Delete again") : t("notes:marks.eraseAgain", "Erase again"),
                    onClick: () => void erase(markId, index),
                  },
                });
              }, { onTimeout: failed, paperId }).catch(() => undefined);
            },
          },
        });
      };
      const erase = (markId: string, index: number): Promise<void> =>
        queueMarkOp((signal) => eraseNow(markId, index, signal), {
          onTimeout: () => toast.error(t("notes:marks.eraseFailed", "Couldn't erase that piece.")),
          paperId: paperOf(markId),
        }).catch(() => undefined);

      /** The newest piece its own mark, after any pending stroke has saved;
       * both marks as the server now has them. */
      const split = (markId: string): Promise<void> =>
        queueMarkOp(async (signal) => {
          const entry = find(markId);
          if (!entry) return;
          const [paperId, mark] = entry;
          let made: SplitResult;
          try {
            made = await splitMark(markId, signal);
          } catch {
            if (!signal.aborted) toast.error(t("notes:marks.splitFailed", "Couldn't split the mark."));
            return;
          }
          const { original, ...fresh } = made;
          replace(paperId, mark, original);
          upsert(paperId, mark, fresh);
          refreshNote(mark.note_id);
          toast(t("notes:marks.splitDone", "Split into its own mark"));
        }, {
          onTimeout: () => toast.error(t("notes:marks.splitFailed", "Couldn't split the mark.")),
          paperId: paperOf(markId),
        }).catch(() => undefined);

      return {
        marksByPaper: {},
        chunksByPaper: {},
        colors: [],
        showMarks: true,
        setShowMarks: (on) => set((s) => ({ showMarks: on, shownForNow: { ...s.shownForNow, marks: false } })),
        shownForNow: { colors: [], marks: false },
        boxOn: false,
        setBoxOn: (on) => set(on ? { boxOn: true, penOn: false, penSession: [] } : { boxOn: false }),
        penOn: false,
        penColor: "green",
        penSession: [],
        setPen: (on, color) =>
          set((s) =>
            on
              ? { penOn: true, penColor: color ?? s.penColor, boxOn: false }
              : { penOn: false, penSession: [] },
          ),
        notePenMark: (mark) =>
          set((s) => (s.penOn ? { penSession: [...s.penSession.filter((m) => m.id !== mark.id), mark] } : {})),

        refetchMarks: async (paperId, signal) => {
          const gen = (refetchGen.get(paperId) ?? 0) + 1;
          refetchGen.set(paperId, gen);
          const since = changeSeq;
          refetchesOut.push(since);
          try {
            const marks = await listMarks(paperId, signal);
            // Aborted (its op timed out) or overtaken: a late snapshot.
            if (signal?.aborted || refetchGen.get(paperId) !== gen) return;
            // Changed here after the request went out: newer than its snapshot.
            const newer = (m: Mark) => (changedAt.get(m.id) ?? -1) > since;
            set((s) => {
              const local = s.marksByPaper[paperId] ?? [];
              const mine = new Map(local.filter(newer).map((m) => [m.id, m]));
              const ids = new Set(marks.map((m) => m.id));
              return {
                marksByPaper: {
                  ...s.marksByPaper,
                  [paperId]: [
                    ...marks.map((m) => mine.get(m.id) ?? m),
                    ...local.filter((m) => newer(m) && !ids.has(m.id)),
                  ],
                },
              };
            });
          } finally {
            refetchesOut.splice(refetchesOut.indexOf(since), 1);
            pruneLocalChanges();
          }
        },

        loadPaper: async (paperId) => {
          const chunks = get().ensureChunks(paperId).catch(() => undefined);
          const marks = await listMarks(paperId);
          set((s) => ({ marksByPaper: { ...s.marksByPaper, [paperId]: marks } }));
          await chunks;
        },

        ensureChunks: (paperId) => {
          const have = get().chunksByPaper[paperId];
          if (have) return Promise.resolve(have);
          let request = chunkRequests.get(paperId);
          if (!request) {
            request = getChunkAnchors(paperId)
              .then((anchors) => {
                const map = chunkMap(anchors);
                set((s) => ({ chunksByPaper: { ...s.chunksByPaper, [paperId]: map } }));
                return map;
              })
              .finally(() => chunkRequests.delete(paperId));
            chunkRequests.set(paperId, request);
          }
          return request;
        },

        loadColors: () => {
          colorsRequest ??= listMarkColors()
            .then((colors) => {
              set({ colors });
              useNotesStore.setState({ colorNames: Object.fromEntries(colors.map((c) => [c.color, c.name])) });
            })
            .catch(() => {
              colorsRequest = null; // try again next time
            });
          return colorsRequest;
        },

        patchColor: async (color, patch) => {
          // Shown or hidden by choice: no longer just for the moment.
          if (patch.visible !== undefined) {
            set((s) => ({ shownForNow: { ...s.shownForNow, colors: s.shownForNow.colors.filter((c) => c !== color) } }));
          }
          const before = get().colors;
          const show = (colors: MarkColorInfo[]) => {
            set({ colors });
            useNotesStore.setState({ colorNames: Object.fromEntries(colors.map((c) => [c.color, c.name])) });
          };
          show(before.map((c) => (c.color === color ? { ...c, ...patch } : c)));
          try {
            show(await patchMarkColor(color, patch));
          } catch {
            show(before);
            toast.error(t("notes:myMarks.saveFailed", "Couldn't save the colour."));
          }
        },

        create: async (body, opts) => {
          let res: MarkCreated;
          try {
            res = await createMark(body, opts?.signal);
          } catch (err) {
            // `silent`: the caller says so itself (the highlighter, with Retry).
            if (!opts?.silent) toast.error(t("notes:marks.saveFailed", "Couldn't save the mark."));
            throw err;
          }
          const saved = res.saved_to;
          const mark: Mark = { ...res.mark, note_id: saved.note_id };
          noteLocalChange(mark.id);
          put(body.paper_content_id, (list) => [...list.filter((m) => m.id !== mark.id), mark]);
          const notes = useNotesStore.getState();
          if (body.hint_block_id && notes.quoteTarget?.blockId === body.hint_block_id) notes.setQuoteTarget(null);
          refreshNote(saved.note_id);
          if (!opts?.quiet) toastSaved(body.paper_content_id, [res]);
          return res;
        },

        remove,
        erase,
        split,
      };
    },
    {
      name: "paperhub-marks-v1",
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({ showMarks: s.showMarks, penColor: s.penColor }),
    },
  ),
);

/** Test isolation: forget the colours request. */
export function resetMarksColorsRequest(): void {
  colorsRequest = null;
}
