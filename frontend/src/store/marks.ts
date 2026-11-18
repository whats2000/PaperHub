/** Reading marks (Plan H5b.1): each paper's marks, the colours and their
 * names, and "Show my marks". A new mark lands as a block in the paper's
 * note on the server; an open notes editor is refreshed from there. */
import type { TFunction } from "i18next";
import { useMemo } from "react";
import { toast } from "sonner";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import { partsOfNote } from "@/components/notes/parts";
import {
  ApiError,
  createMark,
  deleteMark,
  erasePiece,
  getChunkAnchors,
  addMarkColor,
  deleteMarkColor,
  listMarkColors,
  listMarks,
  patchMarkColor,
  reorderMarkColors,
  restoreMark,
  restorePiece,
  splitMark,
} from "@/lib/api";
import i18n from "@/lib/i18n";
import { chunkMap, type ChunkMap } from "@/lib/marks/anchor";
import { livePieceIndexes, type PenMark } from "@/lib/marks/pieces";
import { DEFAULT_PALETTE } from "@/lib/marks/palette";
import { onMarkBlocksChanged, useNotesStore } from "@/store/notes";
import type {
  Mark,
  MarkColor,
  MarkColorInfo,
  MarkColorPatch,
  MarkCreated,
  MarkIn,
  MarkStyle,
  SplitResult,
} from "@/types/notes";

/** The delete notice is short: Ctrl+Z undoes the delete any time later
 * [user, 2026-11-19]. */
const DELETE_NOTICE_MS = 2000;
/** One notice for a mark's delete (and its undo): a new one replaces it. */
const DELETE_TOAST = "mark-deleted";

/** The server no longer has what the client acted on (another tab, the
 * note pane, an earlier request): stale state, not a failure. */
function isStale(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 409 || err.status === 404);
}

/** One undoable mark action in the reader. A split is not one: the server
 * has no way to join two marks back (see the SRS). */
export type MarkAction =
  /** Marks deleted (one, or a note edit's several). */
  | { kind: "delete"; marks: [paperId: number, mark: Mark][] }
  /** A new mark. */
  | { kind: "create"; paperId: number; mark: Mark }
  /** A highlighter stroke joined to an existing mark: its new pieces. */
  | { kind: "join"; paperId: number; markId: string; indexes: number[] }
  /** One piece erased (`deleted`: that left the mark empty, so it went). */
  | { kind: "erase"; paperId: number; mark: Mark; index: number; deleted: boolean }
  /** One user action that made several (a stroke or a selection across
   * sections): undone and redone together. */
  | { kind: "group"; actions: MarkAction[] };

/** The history keeps this many actions (the oldest go first). */
const HISTORY_MAX = 100;

/** The reader's mark actions, newest last: Ctrl+Z undoes the newest, then
 * the one before; Ctrl+Shift+Z / Ctrl+Y redo. No time limit, like any
 * app's undo; kept in memory while the page is open [user, 2026-11-19]. */
let undoStack: MarkAction[] = [];
let redoStack: MarkAction[] = [];

/** Applies an action's inverse (`undo`) or the action again (set by the store). */
let applyHandler: ((action: MarkAction, undo: boolean) => void) | null = null;

/** The actions of the user action in progress (`groupMarkActions`). */
let openGroup: MarkAction[] | null = null;

/** Runs one user action that may make several mark actions (a stroke
 * across sections): they become one history entry, one Ctrl+Z. */
export async function groupMarkActions<T>(run: () => Promise<T>): Promise<T> {
  if (openGroup) return run();
  const group: MarkAction[] = [];
  openGroup = group;
  try {
    return await run();
  } finally {
    openGroup = null;
    if (group.length === 1) record(group[0]!);
    else if (group.length > 1) record({ kind: "group", actions: group });
  }
}

/** A new action: undoable, and what was undone can no longer be redone. */
function record(action: MarkAction): void {
  if (openGroup) {
    openGroup.push(action);
    return;
  }
  undoStack.push(action);
  if (undoStack.length > HISTORY_MAX) undoStack.splice(0, undoStack.length - HISTORY_MAX);
  redoStack = [];
}

/** Undoes one action of the history (already undone: nothing). */
function undoAction(action: MarkAction): void {
  const i = undoStack.indexOf(action);
  if (i < 0) return;
  undoStack.splice(i, 1);
  redoStack.push(action);
  applyHandler?.(action, true);
}

/** Does the newest undone action again. */
function redoLast(): void {
  const action = redoStack.pop();
  if (!action) return;
  undoStack.push(action);
  applyHandler?.(action, false);
}

/** "Removed from the paper and the note", with Undo (of this action) and
 * the Ctrl+Z hint. */
function announceDeleted(action: MarkAction): void {
  const t = i18n.t.bind(i18n) as TFunction;
  toast(t("notes:marks.removedBoth", "Removed from the paper and the note"), {
    id: DELETE_TOAST,
    duration: DELETE_NOTICE_MS,
    description: t("notes:marks.undoHint", "Ctrl+Z to undo"),
    action: { label: t("notes:marks.undo", "Undo"), onClick: () => undoAction(action) },
  });
}

/** Test isolation: forget the undo / redo history. */
export function resetMarkHistory(): void {
  openGroup = null;
  undoStack = [];
  redoStack = [];
}

/** Ctrl+Z (Cmd+Z) outside a text field undoes the newest mark action;
 * Ctrl+Shift+Z and Ctrl+Y redo. A text field (the note editor) keeps its
 * own undo. */
function undoKey(e: KeyboardEvent): void {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
  const key = e.key.toLowerCase();
  const redo = (key === "z" && e.shiftKey) || (key === "y" && !e.shiftKey);
  const undo = key === "z" && !e.shiftKey;
  if (!redo && !undo) return;
  const el = e.target;
  if (el instanceof HTMLElement && (el.isContentEditable || el.closest("input, textarea, select, [contenteditable]"))) {
    return;
  }
  if (undo) {
    const last = undoStack[undoStack.length - 1];
    if (!last) return;
    e.preventDefault();
    undoAction(last);
  } else {
    if (!redoStack.length) return;
    e.preventDefault();
    redoLast();
  }
}
if (typeof window !== "undefined") window.addEventListener("keydown", undoKey);

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
  /** The style a selection is marked in (the popover's choice, remembered). */
  markStyle: MarkStyle;
  setMarkStyle: (style: MarkStyle) => void;
  /** Adds a labelled colour (the label is required); resolves to its key,
   * or rejects with the server's reason. */
  addColor: (hex: string, name: string) => Promise<MarkColor>;
  /** Deletes a custom colour no mark uses (the server refuses otherwise). */
  deleteColor: (color: MarkColor) => Promise<void>;
  /** The palette in this order (shown at once, put back on failure). */
  reorderColors: (order: MarkColor[]) => Promise<void>;
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
  /** Saves a mark (its block lands in the paper's note), with no toast:
   * the card or the pane opens at it; unless `silent`, a failure is said
   * in a toast. */
  create: (body: MarkIn, opts?: { silent?: boolean; signal?: AbortSignal }) => Promise<MarkCreated>;
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
        } catch (err) {
          // Timed out: the delete may have landed; the refetch decides. Stale
          // (already gone): done, the server is asked what it has. Only a
          // definite failure puts the mark back.
          if (signal.aborted) return;
          if (isStale(err)) {
            resync(paperId);
          } else {
            put(paperId, (list) => (list.some((m) => m.id === markId) ? list : [...list, mark]));
            toast.error(t("notes:marks.deleteFailed", "Couldn't delete the mark."));
            return;
          }
        }
        refreshNote(mark.note_id);
        const action: MarkAction = { kind: "delete", marks: [[paperId, mark]] };
        record(action);
        announceDeleted(action);
      };

      /** The server's marks for a paper, quietly (stale state resolved). */
      const resync = (paperId: number) => void get().refetchMarks(paperId).catch(() => undefined);

      /** One step of an undo or redo, in the mark-op queue. Stale state
       * (409/404: the editor's undo, another tab got there first) resyncs
       * quietly; only a real failure says so. */
      const historyStep = (paperId: number, step: (signal: AbortSignal) => Promise<void>) => {
        const failed = () => toast.error(t("notes:marks.historyFailed", "Couldn't change the mark."));
        void queueMarkOp(async (signal) => {
          try {
            await step(signal);
          } catch (err) {
            if (signal.aborted) return;
            if (isStale(err)) resync(paperId);
            else failed();
          }
        }, { onTimeout: failed, paperId }).catch(() => undefined);
      };

      /** A deleted mark and its block back (no toast: it shows). */
      const restoreStep = (paperId: number, mark: Mark) =>
        historyStep(paperId, async (signal) => {
          try {
            await restoreMark(mark.id, signal);
          } catch (err) {
            // Already back (the editor's undo, another tab): the server's copy.
            if (signal.aborted || !isStale(err)) throw err;
            resync(paperId);
          }
          put(paperId, (list) => (list.some((m) => m.id === mark.id) ? list : [...list, mark]));
          refreshNote(mark.note_id);
        });

      /** A mark and its block deleted again, with no notice. */
      const deleteStep = (paperId: number, mark: Mark) =>
        historyStep(paperId, async (signal) => {
          put(paperId, (list) => list.filter((m) => m.id !== mark.id));
          try {
            await deleteMark(mark.id, signal);
          } finally {
            refreshNote(mark.note_id);
          }
        });

      /** One piece erased (`erase`) or put back, as the server then has the mark. */
      const pieceStep = (paperId: number, markId: string, index: number, erase: boolean, fallback?: Mark) =>
        historyStep(paperId, async (signal) => {
          const here = find(markId)?.[1];
          // Erasing a piece that is no longer live here (split off since):
          // nothing to take back; the server's copy decides.
          if (erase && here && !livePieceIndexes(here.pieces).includes(index)) {
            resync(paperId);
            return;
          }
          const res = await (erase ? erasePiece : restorePiece)(markId, index, signal);
          const prev = here ?? fallback;
          if (res.deleted_at !== null) put(paperId, (list) => list.filter((m) => m.id !== markId));
          else if (prev) upsert(paperId, prev, res);
          else resync(paperId);
          refreshNote(prev?.note_id);
        });

      /** An action's inverse (`undo`), or the action again. */
      const applyAction = (action: MarkAction, undo: boolean) => {
        switch (action.kind) {
          case "group":
            // Undo the newest first; redo in the order made.
            for (const a of undo ? [...action.actions].reverse() : action.actions) applyAction(a, undo);
            return;
          case "delete":
            for (const [pid, m] of action.marks) {
              if (undo) restoreStep(pid, m);
              else deleteStep(pid, m);
            }
            return;
          case "create":
            if (undo) deleteStep(action.paperId, action.mark);
            else restoreStep(action.paperId, action.mark);
            return;
          case "join":
            // Undo takes the stroke's pieces off again (newest first).
            for (const i of undo ? [...action.indexes].reverse() : action.indexes) {
              pieceStep(action.paperId, action.markId, i, undo);
            }
            return;
          case "erase":
            pieceStep(action.paperId, action.mark.id, action.index, !undo, action.mark);
            return;
        }
      };

      /** A note change deleted mark blocks or put one back (or the server
       * changed the note out of sight: `change` null): the reader follows
       * at once, and a delete says so with Undo. */
      onMarkBlocksChanged((noteId, change) => {
        const papers = new Set<number>();
        const gone: [number, Mark][] = [];
        for (const [pid, list] of Object.entries(get().marksByPaper)) {
          for (const m of list) {
            if (m.note_id !== noteId) continue;
            papers.add(Number(pid));
            if (change?.deleted.includes(m.block_id)) gone.push([Number(pid), m]);
          }
        }
        for (const [key, id] of Object.entries(useNotesStore.getState().byTarget)) {
          const pid = Number(key.slice("paper:".length));
          if (id === noteId && key.startsWith("paper:") && get().marksByPaper[pid]) papers.add(pid);
        }
        if (change && !gone.length && !change.revived) return;
        for (const [pid, m] of gone) put(pid, (list) => list.filter((x) => x.id !== m.id));
        papers.forEach(resync);
        if (gone.length) {
          const action: MarkAction = { kind: "delete", marks: gone };
          record(action);
          announceDeleted(action);
        }
      });

      // Ctrl+Z and the notice's Undo bring marks back through it.
      applyHandler = applyAction;

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
        } catch (err) {
          if (isStale(err)) resync(paperId);
          else if (!signal.aborted) toast.error(t("notes:marks.eraseFailed", "Couldn't erase that piece."));
          return;
        }
        const deleted = res.deleted_at !== null;
        if (deleted) put(paperId, (list) => list.filter((m) => m.id !== markId));
        else replace(paperId, mark, res);
        refreshNote(mark.note_id);
        // Undone with Ctrl+Z; a piece erased shows on the paper (no toast),
        // a mark the erase emptied gets the delete notice.
        const action: MarkAction = { kind: "erase", paperId, mark, index, deleted };
        record(action);
        if (deleted) announceDeleted(action);
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
          } catch (err) {
            if (isStale(err)) resync(paperId);
            else if (!signal.aborted) toast.error(t("notes:marks.splitFailed", "Couldn't split the mark."));
            return;
          }
          const { original, ...fresh } = made;
          replace(paperId, mark, original);
          upsert(paperId, mark, fresh);
          refreshNote(mark.note_id);
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
        markStyle: "highlight",
        setMarkStyle: (markStyle) => set({ markStyle }),
        addColor: async (hex, name) => {
          const before = new Set(get().colors.map((c) => c.color));
          const colors = await addMarkColor({ hex, name: name.trim() });
          set({ colors });
          useNotesStore.setState({ colorNames: Object.fromEntries(colors.map((c) => [c.color, c.name])) });
          return colors.find((c) => !before.has(c.color))?.color ?? colors[colors.length - 1]!.color;
        },
        deleteColor: async (color) => {
          try {
            const colors = await deleteMarkColor(color);
            set({ colors });
          } catch {
            toast.error(t("notes:myMarks.deleteColorFailed", "Couldn't delete the colour: marks still use it."));
          }
        },
        reorderColors: async (order) => {
          const before = get().colors;
          const byKey = new Map(before.map((c) => [c.color, c]));
          set({ colors: order.map((k) => byKey.get(k)!).filter(Boolean) });
          try {
            set({ colors: await reorderMarkColors(order) });
          } catch {
            set({ colors: before });
            toast.error(t("notes:myMarks.saveFailed", "Couldn't save the colour."));
          }
        },
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
          const before = (get().marksByPaper[body.paper_content_id] ?? []).find((m) => m.id === mark.id);
          put(body.paper_content_id, (list) => [...list.filter((m) => m.id !== mark.id), mark]);
          if (body.join_to && body.join_to === mark.id) {
            // The stroke's pieces were appended: indexes past the old ones.
            const live = livePieceIndexes(mark.pieces);
            const added = before ? live.filter((i) => i >= before.pieces.length) : live.slice(-1);
            if (added.length) record({ kind: "join", paperId: body.paper_content_id, markId: mark.id, indexes: added });
          } else {
            record({ kind: "create", paperId: body.paper_content_id, mark });
          }
          const notes = useNotesStore.getState();
          if (body.hint_block_id && notes.quoteTarget?.blockId === body.hint_block_id) notes.setQuoteTarget(null);
          refreshNote(saved.note_id);
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
      partialize: (s) => ({ showMarks: s.showMarks, penColor: s.penColor, markStyle: s.markStyle }),
    },
  ),
);

/** Every palette colour as `--hl-<key>` (light on :root, dark on .dark), so
 * a custom colour or a recoloured default draws wherever `var(--hl-*)` is
 * used. index.css keeps the defaults for before the palette loads. */
function writePaletteVars(colors: readonly MarkColorInfo[]): void {
  if (typeof document === "undefined") return;
  const own = (c: MarkColorInfo, dark: boolean) =>
    (dark ? c.dark : c.hex) || (dark ? DEFAULT_PALETTE[c.color]?.dark : DEFAULT_PALETTE[c.color]?.light);
  const vars = (dark: boolean) =>
    colors
      .filter((c) => /^[\w-]+$/.test(c.color) && own(c, dark))
      .map((c) => `--hl-${c.color}: ${own(c, dark)};`)
      .join(" ");
  let el = document.getElementById("ph-mark-palette");
  if (!el) {
    el = document.createElement("style");
    el.id = "ph-mark-palette";
    document.head.appendChild(el);
  }
  el.textContent = `:root { ${vars(false)} }\n.dark { ${vars(true)} }`;
}
useMarksStore.subscribe((s, prev) => {
  if (s.colors !== prev.colors) writePaletteVars(s.colors);
});

/** Test isolation: forget the colours request. */
export function resetMarksColorsRequest(): void {
  colorsRequest = null;
}
