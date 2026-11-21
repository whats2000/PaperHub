/** Following a `[[` link (and a `[note:B]` chip): a paper opens in the
 * reader; a paper's note opens with its paper and its note; a chat's note
 * switches to that chat with its note open; an idea's note opens the idea
 * on the Ideas page. A link to a heading then glides to it and flashes it.
 *
 * The reader has no "go to section" request of its own (it jumps to a
 * section only as a citation's fallback), so a link to a paper section
 * opens the paper. */
import { getIdeationGraph, getNote, listIdeationProblems } from "@/lib/api";
import { revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useIdeationStore, type IdeationScope } from "@/store/ideation";
import { useNavStore } from "@/store/nav";
import { linkTargetKey, useNotesStore } from "@/store/notes";
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

/** Follows a link; false when its target could not be opened. */
export async function openNoteLink(link: NoteLinkTarget): Promise<boolean> {
  if (link.targetKind === "paper") {
    useNavStore.getState().setPage("chat");
    useCanvasStore.getState().openPaper(link.targetId);
    return true;
  }
  const noteId = link.targetId;
  let kind: NoteTargetKind;
  let id: number;
  const live = useNotesStore.getState().notes[noteId]?.note;
  const cached = useNotesStore.getState().linkTargets[linkTargetKey("note", noteId)];
  if (live && !live.deleted_at) {
    kind = live.target_kind;
    id = Number(live.target_id);
  } else if (cached?.status === "ok" && cached.noteKind && cached.targetId !== undefined) {
    kind = cached.noteKind;
    id = cached.targetId;
  } else {
    try {
      const d = await getNote(noteId);
      if (d.note.deleted_at) return false;
      kind = d.note.target_kind;
      id = Number(d.note.target_id);
    } catch {
      return false;
    }
  }
  return openNotePlace({ kind, id, noteId, blockId: link.blockId || null });
}

/** Shows a note (and one of its blocks) where it lives. */
export async function openNotePlace(place: NotePlace): Promise<boolean> {
  if (place.kind === "idea") return openIdeaNote(place);
  if (place.kind === "session") {
    const chat = useChatStore.getState().sessions.find((s) => s.backend_session_id === place.id);
    if (!chat) return false;
    // The pane shows only the chat on screen's note: switch chats first.
    if (useChatStore.getState().activeSessionId !== chat.id) useChatStore.getState().selectSession(chat.id);
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
  useNavStore.getState().setPage("ideas");
  const scope = await scopeOfIdea(place.id);
  if (scope === null) return false;
  const ideation = useIdeationStore.getState();
  if (scope !== ideation.scope) ideation.setScope(scope);
  // The scope on screen without the idea (not loaded yet, or older): reload it.
  else if (!ideation.graph?.ideas.some((i) => i.id === place.id)) void ideation.load();
  if (!(await graphHolds(place.id))) return false;
  useIdeationStore.getState().setSelected({ kind: "idea", id: place.id });
  if (place.blockId) await revealBlock(document.body, place.blockId, { unfold: unfoldInNote(place.noteId) });
  return true;
}
