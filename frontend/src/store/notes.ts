/** Notes (Plan H5b.1): loaded notes, the local edit queue and its sync.
 *
 * Edits apply to the local copy at once and are sent as one `postNoteOps`
 * per typing burst (600 ms quiet). Every result's `revision` becomes the base
 * of the next burst: the server rejects an op on a block changed after the
 * caller's base by anyone, so a stale base would reject our own edits.
 * Rejected blocks are replaced by the server's current version and the open
 * editors reload exactly those blocks (`reloads`).
 */
import { toast } from "sonner";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import { applyOpsLocally } from "@/components/notes/sync";
import { getNote, getNoteForTarget, listMarkColors, postNoteOps } from "@/lib/api";
import i18n from "@/lib/i18n";
import type {
  Note,
  NoteBlock,
  NoteComment,
  NoteOp,
  NoteSuggestion,
  NoteTargetKind,
  RejectedOp,
} from "@/types/notes";

export const FLUSH_DELAY_MS = 600;

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
  loadTarget: (kind: NoteTargetKind, id: number) => Promise<number>;
  loadNote: (noteId: number) => Promise<void>;
  applyLocal: (noteId: number, ops: NoteOp[]) => void;
  flush: (noteId: number) => Promise<void>;
  onConflict: (noteId: number, rejected: RejectedOp[]) => void;

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
let colorsRequest: Promise<void> | null = null;

/** Drop every queued edit and pending flush without sending (test isolation). */
export function clearPendingNoteOps(): void {
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  pending.clear();
}

function queue(noteId: number, ops: NoteOp[]) {
  const q = pending.get(noteId) ?? [];
  for (const op of ops) {
    // Typing in one block is many updates: keep only its latest content.
    const same =
      op.op === "update"
        ? q.findIndex((o) => o.op === "update" && o.block_id === op.block_id)
        : -1;
    if (same >= 0) q[same] = op;
    else q.push(op);
  }
  pending.set(noteId, q);
}

function touches(op: NoteOp, ids: Set<string>): boolean {
  return [op.block_id, op.after, op.before, op.parent_id].some((x) => x != null && ids.has(x));
}

function bump(reloads: Record<number, NoteReload>, noteId: number, blockIds: string[] | null) {
  return { ...reloads, [noteId]: { seq: (reloads[noteId]?.seq ?? 0) + 1, blockIds } };
}

export const useNotesStore = create<NotesStore>()(
  persist(
    (set, get) => {
      const schedule = (noteId: number) => {
        clearTimeout(timers.get(noteId));
        timers.set(
          noteId,
          setTimeout(() => {
            timers.delete(noteId);
            void get().flush(noteId);
          }, FLUSH_DELAY_MS),
        );
      };

      const send = async (noteId: number) => {
        const ops = pending.get(noteId);
        const state = get().notes[noteId];
        if (!ops?.length || !state) return;
        pending.delete(noteId);
        try {
          const res = await postNoteOps(noteId, { base_revision: state.revision, ops });
          const applied = new Set(res.applied);
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
          if (res.rejected.length) get().onConflict(noteId, res.rejected);
        } catch {
          // A failed burst leaves the local copy ahead of the server: show
          // the saved version rather than keep edits that were never stored.
          toast.error(i18n.t("notes:saveFailed", "Couldn't save your note. Showing the saved version."));
          pending.delete(noteId);
          await reload(noteId).catch(() => undefined); // offline: the next load catches up
        }
      };

      const reload = async (noteId: number) => {
        const d = await getNote(noteId);
        set((s) => ({
          notes: {
            ...s.notes,
            [noteId]: {
              note: d.note,
              blocks: d.blocks,
              suggestions: d.suggestions,
              comments: d.comments,
              revision: d.note.revision,
            },
          },
          reloads: bump(s.reloads, noteId, null),
        }));
      };

      return {
        notes: {},
        byTarget: {},
        reloads: {},

        loadTarget: async (kind, id) => {
          const known = get().byTarget[`${kind}:${id}`];
          if (known !== undefined) await get().flush(known);
          const d = await getNoteForTarget(kind, id);
          set((s) => {
            const prev = s.notes[d.note.id];
            return {
              notes: {
                ...s.notes,
                [d.note.id]: {
                  note: d.note,
                  blocks: d.blocks,
                  suggestions: prev?.suggestions ?? [],
                  comments: prev?.comments ?? [],
                  revision: d.note.revision,
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
          if (!pending.get(noteId)?.length) return;
          const p = send(noteId);
          inflight.set(noteId, p);
          try {
            await p;
          } finally {
            inflight.delete(noteId);
          }
          if (pending.get(noteId)?.length) schedule(noteId);
        },

        onConflict: (noteId, rejected) => {
          const ids = new Set(rejected.map((r) => r.block_id).filter(Boolean));
          if (!ids.size) return;
          // Queued edits of those blocks were made on the version we lost to.
          const q = pending.get(noteId);
          if (q) pending.set(noteId, q.filter((op) => !touches(op, ids)));
          set((s) => {
            const cur = s.notes[noteId];
            if (!cur) return {};
            let blocks = cur.blocks;
            for (const r of rejected) {
              if (!r.block_id) continue;
              if (r.current) {
                const at = blocks.findIndex((b) => b.id === r.block_id);
                const current = r.current;
                blocks =
                  at >= 0
                    ? blocks.map((b) => (b.id === r.block_id ? current : b))
                    : [...blocks, current];
              } else {
                blocks = applyOpsLocally(noteId, blocks, [{ op: "delete", block_id: r.block_id }]);
              }
            }
            return {
              notes: { ...s.notes, [noteId]: { ...cur, blocks } },
              reloads: bump(s.reloads, noteId, [...ids]),
            };
          });
          toast(i18n.t("notes:conflict", "Updated from another view"));
        },

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
