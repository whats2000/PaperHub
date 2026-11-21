/** Following a `[[` link (and a `[note:B]` chip): a paper opens in the
 * reader (at its section, for a section link); a paper's note opens with
 * its paper and its note; a chat's note switches to that chat with its note
 * open; an idea's note opens the idea on the Ideas page. A link to a
 * heading then glides to it and flashes it. */
import { getIdeationGraph, getSectionRange, listIdeationProblems, listSessions } from "@/lib/api";
import { revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useIdeationStore, type IdeationScope } from "@/store/ideation";
import { useNavStore } from "@/store/nav";
import { linkTargetKey, noteTargetInfo, useNotesStore, type LinkTargetInfo } from "@/store/notes";
import { useNotesPaneStore } from "@/store/notesPane";
import type { NoteTargetKind } from "@/types/notes";

/** A `noteLink`'s target props. */
export interface NoteLinkTarget {
  targetKind: "note" | "paper";
  targetId: number;
  /** A heading's block id ("" none). */
  blockId: string;
  /** A paper section ("" none). */
  sectionKey: string;
}

/** Where a note lives, and the block to show in it. */
export interface NotePlace {
  kind: NoteTargetKind;
  /** The target's id (a paper: paper_content_id; a chat: backend session id). */
  id: number;
  noteId: number;
  blockId: string | null;
}

/** `gone`: the target was deleted; `unreachable`: it could not be found or
 * opened (its chat or idea missing, the lookup failed). */
export type OpenOutcome = "opened" | "gone" | "unreachable";

/** A lookup older than this is checked again before a click follows it. */
export const LINK_RECHECK_MS = 60_000;

/** The target as known now, a stale lookup checked again. */
async function freshTarget(kind: "note" | "paper", id: number): Promise<LinkTargetInfo | undefined> {
  const live = kind === "note" ? useNotesStore.getState().notes[id] : undefined;
  if (live) return noteTargetInfo(live);
  await useNotesStore.getState().resolveLinkTarget(kind, id, { maxAgeMs: LINK_RECHECK_MS });
  return useNotesStore.getState().linkTargets[linkTargetKey(kind, id)];
}

/** Follows a link. */
export async function openNoteLink(link: NoteLinkTarget): Promise<OpenOutcome> {
  const info = await freshTarget(link.targetKind, link.targetId);
  if (info?.status === "dead") return "gone";
  if (link.targetKind === "paper") {
    await openPaperAt(link.targetId, link.sectionKey);
    return "opened";
  }
  if (info?.status !== "ok" || !info.noteKind || info.targetId === undefined) return "unreachable";
  if (link.blockId && !(info.blocks ?? []).some((b) => b.id === link.blockId)) return "gone";
  const place = { kind: info.noteKind, id: info.targetId, noteId: link.targetId, blockId: link.blockId || null };
  return (await openNotePlace(place)) ? "opened" : "unreachable";
}

/** Opens a paper in the reader, at one of its sections (as a citation of
 * the section's chunks); the paper alone when the section is not found. */
export async function openPaperAt(paperId: number, sectionKey: string): Promise<void> {
  useNavStore.getState().setPage("chat");
  if (sectionKey) {
    try {
      const r = await getSectionRange(paperId, sectionKey);
      useCanvasStore.getState().openCitation(r.first_chunk_id, r.last_chunk_id);
      return;
    } catch {
      /* the paper alone */
    }
  }
  useCanvasStore.getState().openPaper(paperId);
}

/** The local chat of a backend session, the chat list synced once if needed. */
async function chatOf(backendId: number): Promise<number | null> {
  const find = () => useChatStore.getState().sessions.find((s) => s.backend_session_id === backendId)?.id ?? null;
  const found = find();
  if (found !== null) return found;
  try {
    useChatStore.getState().reconcileBackendSessions(await listSessions());
  } catch {
    return null;
  }
  return find();
}

/** Shows a note (and one of its blocks) where it lives; false when that
 * place (its chat, its idea) is not there. */
export async function openNotePlace(place: NotePlace): Promise<boolean> {
  if (place.kind === "idea") return openIdeaNote(place);
  if (place.kind === "session") {
    const chat = await chatOf(place.id);
    if (chat === null) return false;
    // The pane shows only the chat on screen's note: switch chats first.
    if (useChatStore.getState().activeSessionId !== chat) useChatStore.getState().selectSession(chat);
    else useNavStore.getState().setPage("chat");
  } else {
    useNavStore.getState().setPage("chat");
  }
  useNotesPaneStore.getState().openAt(place);
  return true;
}

/** The scope (free exploration or a problem) whose graph holds the idea. */
async function scopeOfIdea(ideaId: number): Promise<IdeationScope | null> {
  const st = useIdeationStore.getState();
  if (st.graph?.ideas.some((i) => i.id === ideaId)) return st.scope;
  const problems = st.problems.length ? st.problems : await listIdeationProblems().catch(() => []);
  const scopes: IdeationScope[] = ["explore", ...problems.map((p) => p.id)];
  for (const scope of scopes) {
    if (scope === st.scope && st.graph) continue;
    try {
      const g = await getIdeationGraph(scope);
      if (g.ideas.some((i) => i.id === ideaId)) return scope;
    } catch {
      /* try the next scope */
    }
  }
  return null;
}

/** Resolves once the ideation store's graph holds the idea (false: it never did). */
function graphHolds(ideaId: number, ms = 5000): Promise<boolean> {
  const holds = () => useIdeationStore.getState().graph?.ideas.some((i) => i.id === ideaId) === true;
  if (holds()) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      unsub();
      resolve(false);
    }, ms);
    const unsub = useIdeationStore.subscribe(() => {
      if (!holds()) return;
      clearTimeout(timer);
      unsub();
      resolve(true);
    });
  });
}

/** The Ideas page with the idea selected, the block revealed in its note. */
async function openIdeaNote(place: NotePlace): Promise<boolean> {
  const scope = await scopeOfIdea(place.id);
  if (scope === null) return false;
  useNavStore.getState().setPage("ideas");
  const ideation = useIdeationStore.getState();
  if (scope !== ideation.scope) ideation.setScope(scope);
  // The scope on screen without the idea (not loaded yet, or older): reload it.
  else if (!ideation.graph?.ideas.some((i) => i.id === place.id)) void ideation.load();
  if (!(await graphHolds(place.id))) return false;
  useIdeationStore.getState().setSelected({ kind: "idea", id: place.id });
  if (place.blockId) await revealBlock(document.body, place.blockId, { unfold: unfoldInNote(place.noteId) });
  return true;
}
