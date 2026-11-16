/** Notes (Plan H5b.1): loaded notes, the local edit queue and its sync.
 *
 * Edits apply to the local copy at once and are sent as one `postNoteOps`
 * per typing burst (600 ms quiet). Every result's `revision` becomes the base
 * of the next burst: the server rejects an op on a block changed after the
 * caller's base by anyone, so a stale base would reject our own edits.
 *
 * Nothing the user typed is dropped on the way:
 * - a burst whose request failed (network, 5xx, a lost response) is resent
 *   as-is under the same idempotency key, ahead of anything typed since,
 *   with backoff ("Not saved — retrying"); the server applies a key once;
 * - a burst the server refused (4xx) is dropped and the saved version shown,
 *   with every edit queued since re-applied on top;
 * - a conflicted block is replaced by the server's version; edits queued
 *   beside it stay (re-anchored when their anchor is gone).
 */
import { toast } from "sonner";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import { ApiError, getNote, getNoteForTarget, listMarkColors, postNoteOps } from "@/lib/api";
import i18n from "@/lib/i18n";
import { applyOpsLocally } from "@/lib/notes/ops";
import type {
  Note,
  NoteBlock,
  NoteBlockIn,
  NoteComment,
  NoteOp,
  NoteSuggestion,
  NoteTargetKind,
  RejectedOp,
} from "@/types/notes";

export const FLUSH_DELAY_MS = 600;
/** Waits before each retry of a burst the server could not take. */
export const RETRY_DELAYS_MS = [1000, 2000, 5000, 10000] as const;

export interface NoteState {
  note: Note;
  blocks: NoteBlock[];
  suggestions: NoteSuggestion[];
  comments: NoteComment[];
  /** The base revision of the next burst (the last result's revision). */
  revision: number;
}

/** A bump tells open editors to reload `blockIds` (null = the whole note). */
export interface NoteReload {
  seq: number;
  blockIds: string[] | null;
}

/** Where the next quote from the reader lands ("/" Quote from the paper). */
export interface QuoteTarget {
  noteId: number;
  blockId: string;
}

interface NotesStore {
  notes: Record<number, NoteState>;
  /** `${kind}:${id}` -> note id. */
  byTarget: Record<string, number>;
  reloads: Record<number, NoteReload>;
  /** Notes whose last burst failed and is waiting to be retried. */
  retrying: Record<number, boolean>;
  loadTarget: (kind: NoteTargetKind, id: number) => Promise<number>;
  loadNote: (noteId: number) => Promise<void>;
  applyLocal: (noteId: number, ops: NoteOp[]) => void;
  flush: (noteId: number) => Promise<void>;
  onConflict: (noteId: number, rejected: RejectedOp[]) => Promise<void>;

  activePart: Record<number, string | null>;
  setActivePart: (noteId: number, partId: string | null) => void;
  focusOn: boolean;
  setFocusOn: (on: boolean) => void;
  followOn: boolean;
  setFollowOn: (on: boolean) => void;
  /** Folded divider ids per note. */
  folded: Record<number, string[]>;
  toggleFold: (noteId: number, dividerId: string) => void;
  quoteTarget: QuoteTarget | null;
  setQuoteTarget: (target: QuoteTarget | null) => void;

  /** The user's names for the mark colours (empty until loaded). */
  colorNames: Record<string, string>;
  loadColors: () => Promise<void>;
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

// The edit queue lives outside React state: it is never rendered.
const pending = new Map<number, NoteOp[]>();
const timers = new Map<number, ReturnType<typeof setTimeout>>();
const inflight = new Map<number, Promise<void>>();
/** Failed attempts of the burst at the head of a note's queue. */
const attempts = new Map<number, number>();
/** A burst whose request failed in transit, resent as-is (same ops, base
 * and key) until it lands: if the first try was saved and only its response
 * lost, the server answers the key with that first result. */
interface Burst {
  ops: NoteOp[];
  base: number;
  key: string;
}
const inRetry = new Map<number, Burst>();
let colorsRequest: Promise<void> | null = null;

/** Drop every queued edit and pending flush without sending (test isolation). */
export function clearPendingNoteOps(): void {
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  pending.clear();
  attempts.clear();
  inRetry.clear();
  rebuilds.clear();
}

/** The edits of a note not yet saved, in send order (a copy). */
export function pendingNoteOps(noteId: number): NoteOp[] {
  return [...(inRetry.get(noteId)?.ops ?? []), ...(pending.get(noteId) ?? [])];
}

function newBurstKey(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `b-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function mentions(op: NoteOp, id: string | null | undefined): boolean {
  return id != null && [op.block_id, op.after, op.before, op.parent_id].includes(id);
}

function queue(noteId: number, ops: NoteOp[]) {
  const q = pending.get(noteId) ?? [];
  for (const op of ops) {
    // Typing in one block is many updates: keep only its latest content,
    // unless something later in the queue touches that block (a delete and
    // an undo between two typing runs must keep their order).
    let same = -1;
    if (op.op === "update") {
      for (let i = q.length - 1; i >= 0; i--) {
        if (q[i]!.op === "update" && q[i]!.block_id === op.block_id) {
          same = i;
          break;
        }
      }
      if (same >= 0 && q.slice(same + 1).some((o) => mentions(o, op.block_id))) same = -1;
    }
    if (same >= 0) q[same] = op;
    else q.push(op);
  }
  pending.set(noteId, q);
}

function bump(reloads: Record<number, NoteReload>, noteId: number, blockIds: string[] | null) {
  return { ...reloads, [noteId]: { seq: (reloads[noteId]?.seq ?? 0) + 1, blockIds } };
}

type Placement = Pick<NoteOp, "parent_id" | "after" | "before">;

/** Where a block stood, named by blocks that are still there: after its
 * previous surviving sibling, else before its next one, else under its
 * parent (or where that parent stood). `exclude` is the block being placed. */
function placeInsteadOf(
  blocks: NoteBlock[],
  id: string,
  gone: Set<string>,
  exclude: string | null | undefined,
): Placement {
  const b = blocks.find((x) => x.id === id);
  if (!b) return { parent_id: null };
  const sibs = blocks.filter((x) => x.parent_id === b.parent_id && x.id !== exclude);
  const i = sibs.findIndex((x) => x.id === id);
  const prev = sibs.slice(0, i).reverse().find((x) => !gone.has(x.id));
  if (prev) return { parent_id: b.parent_id, after: prev.id };
  const next = sibs.slice(i + 1).find((x) => !gone.has(x.id));
  if (next) return { parent_id: b.parent_id, before: next.id };
  if (b.parent_id !== null && gone.has(b.parent_id)) {
    return placeInsteadOf(blocks, b.parent_id, gone, exclude);
  }
  return { parent_id: b.parent_id };
}

/** Queued ops placed by a block that is gone, placed where it stood. */
function reanchor(ops: NoteOp[], blocks: NoteBlock[], gone: Set<string>): NoteOp[] {
  return ops.map((op) => {
    const anchors = [op.after, op.before, op.parent_id];
    const lost = anchors.find((a) => a != null && gone.has(a));
    if (lost == null) return op;
    const placed: NoteOp = { ...op };
    delete placed.after;
    delete placed.before;
    delete placed.parent_id;
    return { ...placed, ...placeInsteadOf(blocks, lost, gone, op.block_id) };
  });
}

/** Blocks in document order (parents before children, siblings in order). */
function docOrder(blocks: NoteBlock[]): NoteBlock[] {
  const ids = new Set(blocks.map((b) => b.id));
  const kids = new Map<string | null, NoteBlock[]>();
  for (const b of blocks) {
    const p = b.parent_id !== null && ids.has(b.parent_id) ? b.parent_id : null;
    kids.set(p, [...(kids.get(p) ?? []), b]);
  }
  const out: NoteBlock[] = [];
  const walk = (p: string | null) => {
    for (const b of kids.get(p) ?? []) {
      out.push(b);
      walk(b.id);
    }
  };
  walk(null);
  return out;
}

/** How many times each client-new block's insert has been rebuilt in a row. */
const rebuilds = new Map<string, number>();

/** Fresh inserts for blocks this view created whose insert the server
 * refused (an anchor gone or moved elsewhere): each carries the block as it
 * is now (all its typing), placed in the server's tree after its nearest
 * previous sibling the server has (parent_id taken from that sibling), else
 * first under its parent, else at the end of the section above it, else at
 * the note end. A block refused again goes to the note end; a third refusal
 * stops resending it. */
function freshInserts(
  local: NoteBlock[],
  lost: Set<string>,
  server: NoteBlock[],
): { ops: NoteOp[]; appended: boolean; stopped: boolean } {
  const parentOf = new Map(server.map((b) => [b.id, b.parent_id]));
  const order = docOrder(local);
  const ops: NoteOp[] = [];
  let appended = false;
  let stopped = false;
  order.forEach((b, at) => {
    if (!lost.has(b.id)) return;
    const n = (rebuilds.get(b.id) ?? 0) + 1;
    rebuilds.set(b.id, n);
    if (n > 2) {
      stopped = true;
      return;
    }
    const block: NoteBlockIn = { type: b.type, props: b.props, content: b.content };
    if (b.section_key !== null) block.section_key = b.section_key;
    let placement: Placement & Pick<NoteOp, "under_heading">;
    if (n === 2) {
      placement = { parent_id: null };
      appended = true;
    } else {
      const sibs = local.filter((x) => x.parent_id === b.parent_id);
      const i = sibs.findIndex((x) => x.id === b.id);
      const prev = sibs.slice(0, i).reverse().find((x) => parentOf.has(x.id));
      const heading = order
        .slice(0, at)
        .reverse()
        .find((x) => (x.type === "section" || x.type === "heading") && parentOf.has(x.id));
      if (prev) {
        placement = { parent_id: parentOf.get(prev.id) ?? null, after: prev.id };
      } else if (b.parent_id !== null && parentOf.has(b.parent_id)) {
        const first = server.find((x) => x.parent_id === b.parent_id);
        placement = first ? { parent_id: b.parent_id, before: first.id } : { parent_id: b.parent_id };
      } else if (heading) {
        placement = { under_heading: heading.id };
      } else {
        placement = { parent_id: null };
      }
    }
    ops.push({ op: "insert", block_id: b.id, block, ...placement });
    parentOf.set(
      b.id,
      placement.under_heading ? (parentOf.get(placement.under_heading) ?? null) : (placement.parent_id ?? null),
    );
  });
  return { ops, appended, stopped };
}

export const useNotesStore = create<NotesStore>()(
  persist(
    (set, get) => {
      const schedule = (noteId: number, delay: number = FLUSH_DELAY_MS) => {
        // A burst waiting to be retried keeps its backoff: typing does not
        // hammer a failing server.
        if (attempts.has(noteId) && timers.has(noteId) && delay === FLUSH_DELAY_MS) return;
        clearTimeout(timers.get(noteId));
        timers.set(
          noteId,
          setTimeout(() => {
            timers.delete(noteId);
            void get().flush(noteId);
          }, delay),
        );
      };

      const setRetrying = (noteId: number, on: boolean) => {
        if ((get().retrying[noteId] ?? false) !== on) {
          set((s) => ({ retrying: { ...s.retrying, [noteId]: on } }));
        }
      };

      /** The note as the server sent it with the edits not yet saved put
       * back on top. Queued ops placed by a block the server no longer has
       * (and no queued insert creates) are placed where it stood. */
      const rebase = (
        noteId: number,
        serverBlocks: NoteBlock[],
        prepend?: (server: NoteBlock[]) => NoteOp[],
      ) => {
        const localBlocks = get().notes[noteId]?.blocks ?? [];
        const retry = inRetry.get(noteId)?.ops ?? [];
        const fresh = prepend ? prepend(serverBlocks) : [];
        const queued = [...fresh, ...(pending.get(noteId) ?? [])];
        const serverIds = new Set(serverBlocks.map((b) => b.id));
        const created = new Set(
          [...retry, ...queued].filter((o) => o.op === "insert").map((o) => o.block_id),
        );
        const gone = new Set(
          localBlocks.map((b) => b.id).filter((id) => !serverIds.has(id) && !created.has(id)),
        );
        const q = reanchor(queued, localBlocks, gone);
        if (queued.length) pending.set(noteId, q);
        return {
          blocks: applyOpsLocally(noteId, serverBlocks, [...retry, ...q]),
          unsaved: retry.length + q.length > 0,
        };
      };

      /** Load the saved note and put the edits not yet sent back on top. */
      const reload = async (noteId: number, prepend?: (server: NoteBlock[]) => NoteOp[]) => {
        const d = await getNote(noteId);
        const { blocks, unsaved } = rebase(noteId, d.blocks, prepend);
        set((s) => {
          const cur = s.notes[noteId];
          return {
            notes: {
              ...s.notes,
              [noteId]: {
                note: d.note,
                blocks,
                suggestions: d.suggestions,
                comments: d.comments,
                // Unsaved edits were made on the older base: keep it, so a
                // block changed meanwhile still comes back as a conflict.
                revision: unsaved && cur ? Math.min(cur.revision, d.note.revision) : d.note.revision,
              },
            },
            reloads: bump(s.reloads, noteId, null),
          };
        });
      };

      const resolveConflict = async (
        noteId: number,
        rejected: RejectedOp[],
        sent: NoteOp[],
      ): Promise<void> => {
        const ids = new Set(rejected.map((r) => r.block_id).filter(Boolean));
        const cur = get().notes[noteId];
        if (!ids.size || !cur) return;
        // Blocks this view created whose insert the server refused (an
        // anchor gone or moved in another view): the user's new text. Their
        // stale ops are dropped and one fresh insert per block, carrying its
        // current content, is placed once the note is reloaded.
        const lost = new Set(
          sent
            .filter(
              (o) =>
                o.op === "insert" &&
                o.block_id != null &&
                rejected.some((r) => r.block_id === o.block_id && !r.current),
            )
            .map((o) => o.block_id!),
        );
        // Edits OF a conflicted block were made on the version we lost to;
        // edits placed BESIDE it are the user's words and stay.
        const gone = new Set(
          rejected.filter((r) => r.block_id && !r.current && !lost.has(r.block_id)).map((r) => r.block_id),
        );
        const q = (pending.get(noteId) ?? []).filter((op) => !(op.block_id && ids.has(op.block_id)));
        pending.set(noteId, reanchor(q, cur.blocks, gone));
        let rebuilt = { ops: [] as NoteOp[], appended: false, stopped: false };
        const rebuild = (server: NoteBlock[]) => {
          rebuilt = freshInserts(cur.blocks, lost, server);
          return rebuilt.ops;
        };

        const sectionKept = rejected.some((r) => r.reason === "invalid" && r.current?.type === "section");
        toast(
          sectionKept
            ? i18n.t(
                "notes:sectionNotEmpty",
                "This section still has notes under it. Delete them first, then the heading.",
              )
            : i18n.t("notes:conflict", "Updated from another view"),
        );

        // Where blocks sit (an order change by another view, a move, a
        // block gone) is only known to the server: reload the note, the
        // unsaved edits re-applied on top.
        const notify = () => {
          if (rebuilt.appended) {
            toast(
              i18n.t(
                "notes:insertAppended",
                "A new line could not go where you put it, so it was saved at the end of the note.",
              ),
            );
          }
          if (rebuilt.stopped) {
            toast.error(
              i18n.t("notes:insertNotSaved", "A new line could not be saved. Copy it before you reload."),
            );
          }
        };
        try {
          await reload(noteId, lost.size ? rebuild : undefined);
          notify();
          return;
        } catch {
          /* offline: fall back to the blocks we were given */
        }
        if (lost.size) {
          const known = cur.blocks.filter((b) => !lost.has(b.id) && !gone.has(b.id));
          pending.set(noteId, [...rebuild(known), ...(pending.get(noteId) ?? [])]);
          notify();
        }
        set((s) => {
          const now = s.notes[noteId];
          if (!now) return {};
          let blocks = now.blocks;
          for (const r of rejected) {
            if (!r.block_id || lost.has(r.block_id)) continue;
            const current = r.current;
            if (current) {
              blocks = blocks.some((b) => b.id === r.block_id)
                ? blocks.map((b) => (b.id === r.block_id ? current : b))
                : [...blocks, current];
            } else {
              const gonePlace = placeInsteadOf(blocks, r.block_id, gone, null);
              blocks = blocks
                .filter((b) => b.id !== r.block_id)
                .map((b) => (b.parent_id === r.block_id ? { ...b, parent_id: gonePlace.parent_id ?? null } : b));
            }
          }
          return {
            notes: { ...s.notes, [noteId]: { ...now, blocks } },
            reloads: bump(s.reloads, noteId, null),
          };
        });
      };

      const send = async (noteId: number) => {
        const state = get().notes[noteId];
        if (!state) return;
        let burst = inRetry.get(noteId);
        if (!burst) {
          const ops = pending.get(noteId);
          if (!ops?.length) return;
          pending.delete(noteId);
          burst = { ops, base: state.revision, key: newBurstKey() };
        }
        let res;
        try {
          res = await postNoteOps(noteId, {
            base_revision: burst.base,
            ops: burst.ops,
            idempotency_key: burst.key,
          });
        } catch (err) {
          if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
            // The server refused this burst: it can never land. Show the
            // saved version; edits queued since stay and are re-applied.
            inRetry.delete(noteId);
            attempts.delete(noteId);
            setRetrying(noteId, false);
            toast.error(
              i18n.t("notes:saveRefused", "Couldn't save that change. Showing the saved version."),
            );
            await reload(noteId).catch(() => undefined); // the queue is kept either way
            return;
          }
          // The server could not take it, or took it and the answer was
          // lost (offline, 5xx): resend this very burst later, ahead of
          // anything typed meanwhile.
          inRetry.set(noteId, burst);
          const n = (attempts.get(noteId) ?? 0) + 1;
          attempts.set(noteId, n);
          setRetrying(noteId, true);
          schedule(noteId, RETRY_DELAYS_MS[Math.min(n, RETRY_DELAYS_MS.length) - 1]);
          return;
        }
        inRetry.delete(noteId);
        attempts.delete(noteId);
        setRetrying(noteId, false);
        const applied = new Set(res.applied);
        for (const id of applied) rebuilds.delete(id);
        set((s) => {
          const cur = s.notes[noteId];
          if (!cur) return {};
          return {
            notes: {
              ...s.notes,
              [noteId]: {
                ...cur,
                revision: res.revision,
                note: { ...cur.note, revision: res.revision },
                blocks: cur.blocks.map((b) =>
                  applied.has(b.id) ? { ...b, changed_rev: res.revision } : b,
                ),
              },
            },
          };
        });
        if (res.rejected.length) await resolveConflict(noteId, res.rejected, burst.ops);
      };

      return {
        notes: {},
        byTarget: {},
        reloads: {},
        retrying: {},

        loadTarget: async (kind, id) => {
          const known = get().byTarget[`${kind}:${id}`];
          if (known !== undefined) await get().flush(known);
          const d = await getNoteForTarget(kind, id);
          const { blocks, unsaved } = rebase(d.note.id, d.blocks);
          set((s) => {
            const prev = s.notes[d.note.id];
            return {
              notes: {
                ...s.notes,
                [d.note.id]: {
                  note: d.note,
                  blocks,
                  suggestions: prev?.suggestions ?? [],
                  comments: prev?.comments ?? [],
                  revision:
                    unsaved && prev ? Math.min(prev.revision, d.note.revision) : d.note.revision,
                },
              },
              byTarget: { ...s.byTarget, [`${kind}:${id}`]: d.note.id },
              reloads: prev ? bump(s.reloads, d.note.id, null) : s.reloads,
            };
          });
          return d.note.id;
        },

        loadNote: async (noteId) => {
          await get().flush(noteId);
          await reload(noteId);
        },

        applyLocal: (noteId, ops) => {
          if (!ops.length) return;
          set((s) => {
            const cur = s.notes[noteId];
            if (!cur) return {};
            return {
              notes: {
                ...s.notes,
                [noteId]: { ...cur, blocks: applyOpsLocally(noteId, cur.blocks, ops) },
              },
            };
          });
          queue(noteId, ops);
          schedule(noteId);
        },

        flush: async (noteId) => {
          clearTimeout(timers.get(noteId));
          timers.delete(noteId);
          // One burst at a time: the next one needs this one's revision.
          while (inflight.has(noteId)) await inflight.get(noteId);
          if (!inRetry.has(noteId) && !pending.get(noteId)?.length) return;
          const p = send(noteId);
          inflight.set(noteId, p);
          try {
            await p;
          } finally {
            inflight.delete(noteId);
          }
          if ((inRetry.has(noteId) || pending.get(noteId)?.length) && !timers.has(noteId)) {
            schedule(noteId);
          }
        },

        onConflict: (noteId, rejected) => resolveConflict(noteId, rejected, []),

        activePart: {},
        setActivePart: (noteId, partId) =>
          set((s) => ({ activePart: { ...s.activePart, [noteId]: partId } })),
        focusOn: false,
        setFocusOn: (on) => set({ focusOn: on }),
        followOn: true,
        setFollowOn: (on) => set({ followOn: on }),
        folded: {},
        toggleFold: (noteId, dividerId) =>
          set((s) => {
            const cur = s.folded[noteId] ?? [];
            return {
              folded: {
                ...s.folded,
                [noteId]: cur.includes(dividerId)
                  ? cur.filter((d) => d !== dividerId)
                  : [...cur, dividerId],
              },
            };
          }),
        quoteTarget: null,
        setQuoteTarget: (target) => set({ quoteTarget: target }),

        colorNames: {},
        loadColors: () => {
          colorsRequest ??= listMarkColors()
            .then((rows) => {
              set({ colorNames: Object.fromEntries(rows.map((r) => [r.color, r.name])) });
            })
            .catch(() => {
              colorsRequest = null; // try again next time
            });
          return colorsRequest;
        },
      };
    },
    {
      name: "paperhub-notes-v1",
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({ focusOn: s.focusOn, followOn: s.followOn, folded: s.folded }),
    },
  ),
);
