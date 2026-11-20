/** Saving marks from a reader (Plan H5b.1), shared by the HTML and PDF
 * readers: one mark per section the selection crosses, one confirmation for
 * all of them, a retry that saves only what is still missing, and the note
 * caret after the new block. Highlighter strokes (`usePenSave`) save the
 * same way, one after another with erase and split (`queueMarkOp`). */
import type { TFunction } from "i18next";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import i18n from "@/lib/i18n";
import { joinTarget } from "@/lib/marks/pieces";
import { notesPaneVisible, useMarkNavStore } from "@/store/markNav";
import { isShortQuote } from "@/components/notes/blocks/markCiteText";
import { noteBlockOfMark } from "@/lib/marks/cites";
import {
  groupMarkActions,
  inlineCiteAt,
  markHint,
  queueMarkOp,
  type MarkActionGroup,
  useMarksStore,
} from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkColor, MarkCreated, MarkIn, MarkKind, MarkPiece, MarkStyle } from "@/types/notes";

/** The pieces of one mark and the section it files under. A mark made on
 * a translation also names its language and translation block. */
export interface MarkGroup {
  section: string | null;
  pieces: MarkPiece[];
  lang?: string | null;
  source_ref?: string | null;
}

/** The language and translation block a group's mark was made on. */
function translationOf(g: MarkGroup): Pick<MarkIn, "lang" | "source_ref"> {
  return g.lang ? { lang: g.lang, source_ref: g.source_ref ?? null } : {};
}

export interface MarkSaveRequest {
  kind: MarkKind;
  /** How a text mark is drawn (null for a figure, an equation, a box). */
  style?: MarkStyle | null;
  color: MarkColor | null;
  view: "html" | "pdf";
  /** Open the note card beside the new mark, the caret under its quote
   * (default true; Box is a rapid tool and opens nothing). */
  openCard?: boolean;
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
const progressKey = (
  kind: MarkKind,
  style: MarkStyle | null,
  color: MarkColor | null,
  view: string,
  groups: readonly MarkGroup[],
) => JSON.stringify([kind, style, color, view, groups.map(groupKey)]);

type Create = (body: MarkIn, opts?: { silent?: boolean; group?: MarkActionGroup }) => Promise<MarkCreated>;

/** Saves each group not yet in `done` (one mark per section), in order;
 * throws at the first failure, keeping what was saved. `each` sees every
 * mark as it is saved. One user action: one undo (`groupMarkActions`). */
function saveMissing(
  create: Create,
  body: Omit<MarkIn, "pieces" | "section_key">,
  groups: readonly MarkGroup[],
  done: Map<string, MarkCreated>,
  opts: { silent?: boolean; each?: (res: MarkCreated, index: number) => void; save?: (body: MarkIn, group: MarkActionGroup) => Promise<MarkCreated> } = {},
): Promise<MarkCreated[]> {
  return groupMarkActions(async (markGroup) => {
    for (const [i, group] of groups.entries()) {
      const k = groupKey(group);
      if (done.has(k)) continue;
      const full: MarkIn = {
        ...body,
        pieces: group.pieces,
        section_key: group.section,
        ...translationOf(group),
        ...citeOf(body, group),
      };
      const res = opts.save
        ? await opts.save(full, markGroup)
        : await create(full, { silent: opts.silent, group: markGroup });
      done.set(k, res);
      opts.each?.(res, i);
    }
    return groups.map((g) => done.get(groupKey(g))!);
  });
}

/** A new text mark whose quote is short goes into the note as an inline
 * citation at the remembered caret [user, 2026-11-21]; a longer one, a
 * figure, an equation or a box is a reference block. Read when the mark is
 * saved, so marks in a row follow the caret from one chip to the next. */
function citeOf(body: Omit<MarkIn, "pieces" | "section_key">, group: MarkGroup): Pick<MarkIn, "cite_at"> {
  if (body.kind !== "text" || body.join_to) return {};
  const quote = group.pieces.map((p) => (typeof p.quote === "string" ? p.quote : "")).join(" ");
  if (!isShortQuote(quote)) return {};
  const at = inlineCiteAt(body.paper_content_id);
  return at ? { cite_at: at } : {};
}

/** Opens a mark's block in the notes pane (until the note card). */
export function openMarkInNotes(paperId: number, mark: Mark | undefined): void {
  if (!mark || mark.note_id === undefined) return;
  const blocks = useNotesStore.getState().notes[mark.note_id]?.blocks;
  useNotesPaneStore
    .getState()
    .openAt({ kind: "paper", id: paperId, noteId: mark.note_id, blockId: noteBlockOfMark(blocks, mark) });
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
    async ({ kind, style = null, color, view, openCard = true, groups: getGroups }: MarkSaveRequest): Promise<boolean> => {
      if (paperId === null || busyRef.current) return false;
      busyRef.current = true;
      setBusy(true);
      try {
        const groups = await getGroups();
        if (!groups?.length) return false;
        const key = progressKey(kind, style, color, view, groups);
        if (progress.current?.key !== key) progress.current = { key, saved: new Map() };
        // A retry after a failure saves only the sections still missing.
        const results = await saveMissing(
          create,
          { paper_content_id: paperId, kind, style: kind === "text" ? style : null, color, view, hint_block_id: markHint(paperId) },
          groups,
          progress.current.saved,
        );
        progress.current = null;
        const last = results[results.length - 1];
        if (last && openCard) openAfterMark(paperId, last);
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

/** After a mark is made, where the user writes about it: with the notes
 * pane on screen its new block scrolls into view there (flashed), the caret
 * on a new line under it, the reader staying; with the pane hidden the note
 * card opens beside the mark, the caret under its quote [user, 2026-11-19]. */
export function openAfterMark(paperId: number, saved: MarkCreated): void {
  // An inline citation: the caret right after the chip, its text field
  // closed; the pane scrolls to it, the card shows the part holding it.
  const target = saved.cite
    ? { noteId: saved.saved_to.note_id, blockId: saved.cite.block_id, offset: saved.cite.offset, markId: saved.mark.id }
    : { noteId: saved.saved_to.note_id, blockId: saved.mark.block_id };
  if (notesPaneVisible()) {
    useNotesStore.getState().setCaretAfter(target);
    useNotesPaneStore.getState().openAt({ kind: "paper", id: paperId, ...target });
  } else {
    useNotesStore.getState().setCaretAfter({ ...target, in: "card" });
    useMarkNavStore.getState().openCard(paperId, saved.mark.id);
  }
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

/** A piece as compared with the server's copy: without what the server
 * adds or changes later (its stroke, erased / lost flags). */
const SERVER_KEYS = new Set(["stroke", "erased", "removed", "lost"]);

function pieceKey(p: MarkPiece): string {
  return JSON.stringify(
    Object.keys(p)
      .filter((k) => !SERVER_KEYS.has(k))
      .sort()
      .map((k) => [k, p[k]]),
  );
}

/** The server's mark that already holds every piece of `body` (an earlier
 * attempt that landed but whose answer timed out), as a save result. */
function alreadyOnServer(paperId: number, body: MarkIn): MarkCreated | null {
  const want = body.pieces.map(pieceKey);
  const mark = (useMarksStore.getState().marksByPaper[paperId] ?? []).find((m) => {
    if (
      m.deleted_at !== null ||
      m.kind !== body.kind ||
      (m.style ?? null) !== (body.style ?? null) ||
      m.color !== body.color ||
      m.view !== body.view
    ) {
      return false;
    }
    // A piece the server lost (after a re-ingest) is not held.
    const have = new Set(m.pieces.filter((x) => !x.erased && !x.removed && !x.lost).map(pieceKey));
    return want.every((k) => have.has(k));
  });
  if (!mark) return null;
  return {
    mark,
    block: null,
    saved_to: {
      note_id: mark.note_id ?? 0,
      paper_title: "",
      section: mark.section_key,
      part_index: 0,
      part_count: 1,
    },
  };
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

      const retry = { label: t("notes:marks.retry", "Retry"), onClick: () => enqueue() };
      const failed = () => toast.error(t("notes:marks.saveFailed", "Couldn't save the mark."), { action: retry });
      // Still finding where the stroke is (a PDF locate, the chunk map), or
      // saving it: a timeout says which.
      let placing = true;

      const run = async (signal: AbortSignal): Promise<void> => {
        if (paperId === null) return stroke.done?.(false);
        placing = !placedOnce;
        const placed = placedOnce ?? (await stroke.pieces());
        if (!placed?.length) return stroke.done?.(false);
        placedOnce = placed;
        placing = false;
        // A stroke drawn just before the pen went off still saves; it only
        // joins nothing (the session ended).
        const { penSession, marksByPaper, notePenMark } = useMarksStore.getState();
        const liveIds = new Set((marksByPaper[paperId] ?? []).filter((m) => m.deleted_at === null).map((m) => m.id));
        const session = penSession.filter((m) => liveIds.has(m.id));
        // The pen draws highlights (a figure or an equation is taken whole).
        const kind = placed[0]!.kind ?? stroke.kind ?? "text";
        const style: MarkStyle | null = kind === "text" ? "highlight" : null;
        const one = placed.length === 1 ? placed[0]! : null;
        const target = kind === "text" && one ? joinTarget(session, { color, place: one.place }) : null;
        // Consecutive pieces under one section make one mark; a mark made
        // on a translation holds one translated paragraph.
        const groups: (MarkGroup & { places: string[] })[] = [];
        for (const p of placed) {
          const last = groups[groups.length - 1];
          const lang = typeof p.piece.lang === "string" ? p.piece.lang : null;
          const ref = typeof p.piece.source_ref === "string" ? p.piece.source_ref : null;
          if (last && last.section === p.section && (last.lang ?? null) === lang && (last.source_ref ?? null) === ref) {
            last.pieces.push(p.piece);
            if (p.place) last.places.push(p.place);
          } else {
            groups.push({
              section: p.section,
              pieces: [p.piece],
              places: p.place ? [p.place] : [],
              ...(lang ? { lang, source_ref: ref } : {}),
            });
          }
        }
        const plain = groups.map(({ places: _places, ...group }) => group);
        const key = progressKey(kind, style, color, stroke.view, plain);
        const done = savedOnce ?? progress.current.get(key) ?? new Map<string, MarkCreated>();
        savedOnce = done;
        progress.current.set(key, done);
        // Set only when this run itself joined a piece to a mark.
        let joinedNow: string | null = null;
        // What this run itself saved (none: it was all there already).
        const savedNow: MarkCreated[] = [];
        const opts = { silent: true, signal };
        try {
          await saveMissing(
            create,
            { paper_content_id: paperId, kind, style, color, view: stroke.view, hint_block_id: markHint(paperId) },
            plain,
            done,
            {
              silent: true,
              save: async (body, group) => {
                const there = alreadyOnServer(paperId, body);
                // The server already has it (an earlier attempt landed but
                // timed out; the queue refetched the marks since).
                if (there) return there;
                const res = await (async () => {
                  if (!target) return create(body, { ...opts, group });
                  try {
                    const joined = await create({ ...body, join_to: target }, { ...opts, group });
                    joinedNow = target;
                    return joined;
                  } catch (err) {
                    if (!refusedJoin(err)) throw err;
                    return create(body, { ...opts, group });
                  }
                })();
                savedNow.push(res);
                return res;
              },
              // In the session as soon as it is saved: a stroke that fails
              // later still lets the next stroke join what did save.
              each: (res, i) => {
                if (kind !== "text") return;
                const prev = session.find((m) => m.id === res.mark.id);
                notePenMark({ id: res.mark.id, color, places: [...(prev?.places ?? []), ...groups[i]!.places] });
              },
            },
          );
          progress.current.delete(key);
          // A stroke is a rapid tool: no toast for a plain save (the mark
          // shows on the paper); a join says so with its Split.
          if (joinedNow) toastJoined(joinedNow);
          // A Retry that finds it all saved says so.
          else if (!savedNow.length) toast(t("notes:marks.alreadySaved", "Already saved"));
          // Like a mark from the menu: its note opens to write in.
          const last = savedNow[savedNow.length - 1];
          if (last && kind === "text") openAfterMark(paperId, last);
          stroke.done?.(true);
        } catch {
          // What saved stays saved; Retry saves the rest. A timeout already
          // said so (queueMarkOp).
          if (!signal.aborted) failed();
          stroke.done?.(false);
        }
      };
      const enqueue = () =>
        void queueMarkOp(run, {
          onTimeout: () => {
            if (placing) {
              toast.error(
                t("notes:marks.placeTimeout", "Couldn't find where this passage is in the paper in time."),
                { action: retry },
              );
            } else failed();
            stroke.done?.(false);
          },
          // The create may have landed: the queue refetches the paper's
          // marks before anything else (a Retry included) runs.
          paperId,
        }).catch(() => undefined);
      enqueue();
    },
    [paperId, create],
  );
}
