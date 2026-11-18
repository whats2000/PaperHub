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

import {
  ApiError,
  createNoteComment,
  getNote,
  getNoteForTarget,
  listMarkColors,
  postNoteOps,
  replyToNoteComment,
  resolveSuggestion as postSuggestionDecision,
  setNoteCommentStatus,
} from "@/lib/api";
import i18n from "@/lib/i18n";
import { applyOpsLocally, blockText } from "@/lib/notes/ops";
import type {
  ApplyResult,
  Note,
  NoteBlock,
  NoteBlockIn,
  NoteComment,
  NoteCommentStatus,
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
  /** How many live chats the note is linked to (unknown until loaded). */
  sharedBy?: number;
}

/** An edit one editor made: the blocks it changed (another editor shows them). */
export interface LocalEdit {
  seq: number;
  origin: string;
  blockIds: string[];
}

/** Blocks the server saved under another id than the one sent: open
 * editors re-key them in place (`pairs`: [sent id, saved id]). */
export interface NoteRenames {
  seq: number;
  pairs: [string, string][];
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
  renames: Record<number, NoteRenames>;
  /** The last edit an editor made to each note (`origin`: which editor),
   * so another editor open on the same note (the note card and the notes
   * pane) takes the changed blocks in place. */
  localEdits: Record<number, LocalEdit>;
  /** Notes whose last burst failed and is waiting to be retried. */
  retrying: Record<number, boolean>;
  /** New blocks the server would not take, kept here unsaved: per note,
   * block id -> the insert that would save it. Re-applied over every
   * reload, never sent until the user edits one (one more try). */
  drafts: Record<number, Record<string, NoteOp>>;
  loadTarget: (kind: NoteTargetKind, id: number) => Promise<number>;
  loadNote: (noteId: number) => Promise<void>;
  applyLocal: (noteId: number, ops: NoteOp[], origin?: string) => void;
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
  /** Forgets the folds of dividers no longer in the note (deleted, or turned
   * back into text). */
  keepFolds: (noteId: number, dividerIds: ReadonlySet<string>) => void;
  quoteTarget: QuoteTarget | null;
  setQuoteTarget: (target: QuoteTarget | null) => void;
  /** "Add note" on a reader selection: once the editor shows this block it
   * puts the caret on a line after it, then clears the request (dropped
   * after CARET_GIVE_UP_MS if the block never shows). */
  caretAfter: QuoteTarget | null;
  setCaretAfter: (target: QuoteTarget | null) => void;

  /** The user's names for the mark colours (empty until loaded). */
  colorNames: Record<string, string>;
  loadColors: () => Promise<void>;

  /** Starts a thread on `blockId` (sends the queued edits first: the block
   * must exist on the server). Returns the new thread. */
  addComment: (noteId: number, blockId: string, quote: string, text: string) => Promise<NoteComment>;
  replyComment: (noteId: number, commentId: string, text: string) => Promise<NoteComment>;
  /** `deleted` drops the thread from the list; `open` lists it again. */
  setCommentStatus: (noteId: number, commentId: string, status: NoteCommentStatus) => Promise<void>;

  /** Accepts or rejects one of the agent's suggestions (edits queued
   * before it are sent first), then shows the saved note — after `exit`
   * (the change animating out, told the server's result), when given. */
  resolveSuggestion: (
    noteId: number,
    suggestionId: string,
    accept: boolean,
    exit?: (res: ApplyResult) => Promise<void>,
  ) => Promise<ApplyResult>;
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
let caretTimer: ReturnType<typeof setTimeout> | undefined;
/** How long a caret request waits for its block (as "Open in notes" does). */
export const CARET_GIVE_UP_MS = 5000;

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

/** `op` with every id in `ids` replaced by its new id. */
function rekeyOp(op: NoteOp, ids: Map<string, string>): NoteOp {
  let out = op;
  for (const f of ["block_id", "parent_id", "after", "before", "under_heading"] as const) {
    const v = op[f];
    if (typeof v === "string" && ids.has(v)) out = { ...out, [f]: ids.get(v)! };
  }
  return out;
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
 * makes it an unsaved draft (`drafted`) instead of resending it. */
function freshInserts(
  local: NoteBlock[],
  lost: Set<string>,
  server: NoteBlock[],
): { ops: NoteOp[]; appended: boolean; drafted: NoteBlock[] } {
  const parentOf = new Map(server.map((b) => [b.id, b.parent_id]));
  const order = docOrder(local);
  const ops: NoteOp[] = [];
  let appended = false;
  const drafted: NoteBlock[] = [];
  order.forEach((b, at) => {
    if (!lost.has(b.id)) return;
    const n = (rebuilds.get(b.id) ?? 0) + 1;
    rebuilds.set(b.id, n);
    if (n > 2) {
      drafted.push(b);
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
  return { ops, appended, drafted };
}

/** A new block id, the editor's shape (a UUID). */
function newBlockId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `d-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** For each block whose sent update the server refused while it kept (or
 * deleted) its own version, the user's version as a new block right after
 * it: `local` is the local blocks with those copies in place. */
function keptVersions(
  local: NoteBlock[],
  rejected: RejectedOp[],
  sent: NoteOp[],
  lost: Set<string>,
): { local: NoteBlock[]; drafts: NoteBlock[] } {
  const drafts: NoteBlock[] = [];
  let out = local;
  for (const r of rejected) {
    if (!r.block_id || lost.has(r.block_id)) continue;
    if (!sent.some((o) => o.op === "update" && o.block_id === r.block_id)) continue;
    const mine = out.find((b) => b.id === r.block_id);
    if (!mine || !blockText(mine.type, mine.props, mine.content).trim()) continue;
    if (r.current && JSON.stringify(r.current.content) === JSON.stringify(mine.content)
        && JSON.stringify(r.current.props) === JSON.stringify(mine.props)) continue;
    const copy: NoteBlock = { ...mine, id: newBlockId(), author: "user", changed_rev: 0 };
    const at = out.indexOf(mine);
    // Right after the server's version; in the place of a deleted block.
    out = r.current ? [...out.slice(0, at + 1), copy, ...out.slice(at + 1)] : [...out.slice(0, at), copy, ...out.slice(at + 1)];
    drafts.push(copy);
  }
  return { local: out, drafts };
}

/** The insert that would save a draft, placed where it stands locally. */
function draftInsert(local: NoteBlock[], b: NoteBlock): NoteOp {
  const block: NoteBlockIn = { type: b.type, props: b.props, content: b.content };
  if (b.section_key !== null) block.section_key = b.section_key;
  const sibs = local.filter((x) => x.parent_id === b.parent_id);
  const prev = sibs[sibs.findIndex((x) => x.id === b.id) - 1];
  return prev
    ? { op: "insert", block_id: b.id, block, parent_id: b.parent_id, after: prev.id }
    : { op: "insert", block_id: b.id, block, parent_id: b.parent_id };
}

export const useNotesStore = create<NotesStore>()(
  persist(
    (set, get) => {
      const writeComments = (noteId: number, f: (list: NoteComment[]) => NoteComment[]) =>
        set((s) => {
          const cur = s.notes[noteId];
          return cur ? { notes: { ...s.notes, [noteId]: { ...cur, comments: f(cur.comments) } } } : {};
        });

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

      /** Keep new blocks the server would not take as unsaved drafts. */
      const keepDrafts = (noteId: number, local: NoteBlock[], blocks: NoteBlock[]) => {
        if (!blocks.length) return;
        const ids = new Set(blocks.map((b) => b.id));
        // Their queued edits are part of the draft now.
        const q = pending.get(noteId);
        if (q) pending.set(noteId, q.filter((o) => !(o.block_id && ids.has(o.block_id))));
        set((s) => ({
          drafts: {
            ...s.drafts,
            [noteId]: {
              ...(s.drafts[noteId] ?? {}),
              ...Object.fromEntries(blocks.map((b) => [b.id, draftInsert(local, b)])),
            },
          },
        }));
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
          blocks: applyOpsLocally(noteId, serverBlocks, [
            ...retry,
            ...q,
            ...Object.values(get().drafts[noteId] ?? {}),
          ]),
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
                sharedBy: d.shared_by,
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
        // The server's version wins (another view, or the agent, changed or
        // deleted the block first), but what the user typed into it stays on
        // screen: a copy of their version right after it, an unsaved draft.
        const typed = keptVersions(cur.blocks, rejected, sent, lost);
        if (typed.drafts.length) keepDrafts(noteId, typed.local, typed.drafts);
        let rebuilt = { ops: [] as NoteOp[], appended: false, drafted: [] as NoteBlock[] };
        const rebuild = (server: NoteBlock[]) => {
          rebuilt = freshInserts(cur.blocks, lost, server);
          keepDrafts(noteId, cur.blocks, rebuilt.drafted);
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
          if (rebuilt.drafted.length) {
            toast(
              i18n.t(
                "notes:insertNotSaved",
                "A new line could not be saved. It stays here, marked Not saved; edit it to try again.",
              ),
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
          if (typed.drafts.length) {
            blocks = applyOpsLocally(
              noteId,
              blocks,
              typed.drafts.map((d) => draftInsert(typed.local, d)),
            );
          }
          return {
            notes: { ...s.notes, [noteId]: { ...now, blocks } },
            reloads: bump(s.reloads, noteId, null),
          };
        });
      };

      /** Blocks the server saved under a new id (one pasted from another
       * note kept its id there): re-key them here, in the edits queued
       * since and in the open editors, so the next edit finds them. */
      const rekey = (noteId: number, renamed: { from: string; to: string }[]) => {
        const ids = new Map(renamed.map((r) => [r.from, r.to]));
        const q = pending.get(noteId);
        if (q) pending.set(noteId, q.map((o) => rekeyOp(o, ids)));
        for (const [from, to] of ids) {
          const n = rebuilds.get(from);
          rebuilds.delete(from);
          if (n !== undefined) rebuilds.set(to, n);
        }
        const re = (id: string | null) => (id !== null ? (ids.get(id) ?? id) : id);
        set((s) => {
          const cur = s.notes[noteId];
          if (!cur) return {};
          const drafts = s.drafts[noteId];
          return {
            notes: {
              ...s.notes,
              [noteId]: {
                ...cur,
                blocks: cur.blocks.map((b) =>
                  ids.has(b.id) || (b.parent_id !== null && ids.has(b.parent_id))
                    ? { ...b, id: re(b.id)!, parent_id: re(b.parent_id) }
                    : b,
                ),
              },
            },
            drafts: drafts
              ? {
                  ...s.drafts,
                  [noteId]: Object.fromEntries(
                    Object.entries(drafts).map(([id, op]) => [re(id)!, rekeyOp(op, ids)]),
                  ),
                }
              : s.drafts,
            quoteTarget:
              s.quoteTarget?.noteId === noteId && ids.has(s.quoteTarget.blockId)
                ? { noteId, blockId: ids.get(s.quoteTarget.blockId)! }
                : s.quoteTarget,
            renames: {
              ...s.renames,
              [noteId]: { seq: (s.renames[noteId]?.seq ?? 0) + 1, pairs: [...ids] },
            },
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
            // New blocks in the refused burst are the user's new text: keep
            // them as unsaved drafts rather than lose them to the reload.
            const local = get().notes[noteId]?.blocks ?? [];
            const created = new Set(burst.ops.filter((o) => o.op === "insert").map((o) => o.block_id));
            const fresh = local.filter((b) => created.has(b.id));
            keepDrafts(noteId, local, fresh);
            if (fresh.length) {
              toast(
                i18n.t("notes:draftsKept", "New lines that could not be saved stay here, marked Not saved."),
              );
            }
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
        if (res.renamed?.length) rekey(noteId, res.renamed);
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
        renames: {},
        localEdits: {},
        retrying: {},
        drafts: {},

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
                  suggestions: d.suggestions ?? prev?.suggestions ?? [],
                  comments: d.comments ?? prev?.comments ?? [],
                  sharedBy: d.shared_by,
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

        applyLocal: (noteId, ops, origin) => {
          if (!ops.length) return;
          set((s) => {
            const cur = s.notes[noteId];
            if (!cur) return {};
            const blockIds = [...new Set(ops.map((o) => o.block_id).filter((id): id is string => !!id))];
            return {
              notes: {
                ...s.notes,
                [noteId]: { ...cur, blocks: applyOpsLocally(noteId, cur.blocks, ops) },
              },
              localEdits: origin
                ? { ...s.localEdits, [noteId]: { seq: (s.localEdits[noteId]?.seq ?? 0) + 1, origin, blockIds } }
                : s.localEdits,
            };
          });
          // An edit of an unsaved draft is one more try at saving it; a
          // delete simply drops it (the server never had it).
          const drafts = get().drafts[noteId] ?? {};
          const touched = new Set(
            ops.map((o) => o.block_id).filter((id): id is string => !!id && id in drafts),
          );
          if (!touched.size) {
            queue(noteId, ops);
          } else {
            const local = get().notes[noteId]?.blocks ?? [];
            const rest = Object.fromEntries(Object.entries(drafts).filter(([id]) => !touched.has(id)));
            set((s) => ({ drafts: { ...s.drafts, [noteId]: rest } }));
            const retry = new Set([...touched].filter((id) => local.some((b) => b.id === id)));
            for (const id of retry) rebuilds.set(id, 0);
            const known = local.filter((b) => !(b.id in rest) && !retry.has(b.id));
            const fresh = freshInserts(local, retry, known).ops;
            // Refused again, it becomes a draft again rather than moving.
            for (const id of retry) rebuilds.set(id, 2);
            queue(noteId, [...fresh, ...ops.filter((o) => !(o.block_id && touched.has(o.block_id)))]);
          }
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
        keepFolds: (noteId, dividerIds) => {
          const cur = get().folded[noteId] ?? [];
          const kept = cur.filter((d) => dividerIds.has(d));
          if (kept.length !== cur.length) set((s) => ({ folded: { ...s.folded, [noteId]: kept } }));
        },
        quoteTarget: null,
        setQuoteTarget: (target) => set({ quoteTarget: target }),
        caretAfter: null,
        setCaretAfter: (target) => {
          set({ caretAfter: target });
          clearTimeout(caretTimer);
          // A block that never shows (the save was undone, another note
          // opened) must not move the caret on a later visit.
          if (target) {
            caretTimer = setTimeout(() => {
              if (get().caretAfter === target) set({ caretAfter: null });
            }, CARET_GIVE_UP_MS);
          }
        },

        addComment: async (noteId, blockId, quote, text) => {
          await get().flush(noteId);
          const thread = await createNoteComment(noteId, { block_id: blockId, quote, text });
          writeComments(noteId, (list) => [...list, thread]);
          return thread;
        },
        replyComment: async (noteId, commentId, text) => {
          const thread = await replyToNoteComment(commentId, text);
          writeComments(noteId, (list) => list.map((c) => (c.id === commentId ? thread : c)));
          return thread;
        },
        setCommentStatus: async (noteId, commentId, status) => {
          const thread = await setNoteCommentStatus(commentId, status);
          writeComments(noteId, (list) => {
            if (status === "deleted") return list.filter((c) => c.id !== commentId);
            // A thread brought back after Delete is not in the list any more.
            return list.some((c) => c.id === commentId)
              ? list.map((c) => (c.id === commentId ? thread : c))
              : [...list, thread].sort((a, b) => a.created_at.localeCompare(b.created_at));
          });
        },

        resolveSuggestion: async (noteId, suggestionId, accept, exit) => {
          await get().flush(noteId);
          const res = await postSuggestionDecision(suggestionId, accept);
          await exit?.(res);
          await reload(noteId);
          return res;
        },

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
