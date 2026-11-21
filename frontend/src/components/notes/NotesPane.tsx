/** The Notes pane of the chat's right panel: the note of the paper in the
 * reader, or of this chat, in the full editor. */
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { REVEAL_GIVE_UP_MS, revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { cn } from "@/lib/utils";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useNotesPaneStore } from "@/store/notesPane";
import { isUnsavedNote, useNotesStore } from "@/store/notes";

import { NoteEditor } from "./NoteEditor";
import { notePanelId, noteTabId } from "./noteTabIds";
import { NotesTabs, type NoteTab } from "./NotesTabs";
import { NotesToolbar } from "./NotesToolbar";
import { useFollowReading } from "./useFollowReading";
import { useNoteExport } from "./useNoteExport";

interface Props {
  onClose: () => void;
}

/** The chat tab's target is the chat's local id. */
type Target = { kind: "paper" | "chat"; id: number };

/** A chat's note request shows only while that chat is on screen. */
function requestShowsHere(request: { kind: string; id: number }, backendSessionId: number | null): boolean {
  return request.kind !== "session" || request.id === backendSessionId;
}

export function NotesPane({ onClose }: Props) {
  const { t } = useTranslation("notes");
  const idBase = useId();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const session = useChatStore((s) =>
    s.activeSessionId === null ? null : (s.sessions.find((x) => x.id === s.activeSessionId) ?? null),
  );
  const backendSessionId = session?.backend_session_id ?? null;
  const readerPaperId = useCanvasStore((s) => s.activePaperId);

  // The paper last shown in the reader stays the paper tab after the reader
  // closes; a new chat starts without one.
  const [paper, setPaper] = useState({ id: readerPaperId, session: activeSessionId });
  if (paper.session !== activeSessionId) {
    setPaper({ id: readerPaperId, session: activeSessionId });
  } else if (readerPaperId !== null && readerPaperId !== paper.id) {
    setPaper({ id: readerPaperId, session: activeSessionId });
  }
  const paperId = paper.id;

  const [chosen, setChosen] = useState<NoteTab>("paper");
  // "Open in notes" (the agent's change card): the note's tab comes forward.
  const request = useNotesPaneStore((s) => s.request);
  const consumeRequest = useNotesPaneStore((s) => s.consumeRequest);
  const [seenRequest, setSeenRequest] = useState(0);
  if (request && request.seq !== seenRequest) {
    setSeenRequest(request.seq);
    if (request.kind === "session") {
      if (requestShowsHere(request, backendSessionId)) setChosen("chat");
    }
    else if (request.kind === "paper") setChosen("paper");
  }
  const tab: NoteTab = paperId === null ? "chat" : session === null ? "paper" : chosen;
  // The chat tab names the chat by its local id: it stays the same note
  // when the chat reaches the server (its first message, or the note's
  // first write).
  const target: Target | null =
    tab === "paper" && paperId !== null
      ? { kind: "paper", id: paperId }
      : session !== null && activeSessionId !== null
        ? { kind: "chat", id: activeSessionId }
        : null;

  const refTitle = useChatStore((s) =>
    backendSessionId === null || paperId === null
      ? undefined
      : s.referencesBySession[backendSessionId]?.find((r) => r.paper_content_id === paperId)?.title,
  );
  const paperNoteTitle = useNotesStore((s) => {
    const id = paperId === null ? undefined : s.byTarget[`paper:${paperId}`];
    // The paper's own title, not the note's (renamable) page title.
    return id === undefined ? undefined : s.notes[id]?.defaultTitle || s.notes[id]?.note.title;
  });
  const paperTitle =
    paperId === null ? null : (refTitle ?? paperNoteTitle ?? t("pane.paper", "Paper"));

  // `storeKey`: the store's `byTarget` key the note is shown under; it
  // follows a placeholder to its saved note (nothing is created by
  // looking: an unsaved note is a placeholder until its first write).
  const [loaded, setLoaded] = useState<{ key: string; storeKey: string | null; failed: boolean }>({
    key: "",
    storeKey: null,
    failed: false,
  });
  const targetKey = target ? `${target.kind}:${target.id}` : "";
  const loadTarget = useNotesStore((s) => s.loadTarget);
  const openChatDraft = useNotesStore((s) => s.openChatDraft);

  // A chat's note started while the chat was local (only on this page)
  // keeps showing after the chat reaches the server.
  const draftKey = target?.kind === "chat" ? `local:${target.id}` : null;
  const hasDraft = useNotesStore((s) => draftKey !== null && s.byTarget[draftKey] !== undefined);
  const localChat = target?.kind === "chat" && backendSessionId === null;

  useEffect(() => {
    if (!target || hasDraft) return;
    if (target.kind === "chat" && backendSessionId === null) {
      // A chat not on the server: an empty note, saved (chat first) by
      // the first write.
      openChatDraft(target.id);
      return;
    }
    let cancelled = false;
    const kind = target.kind === "chat" ? "session" : "paper";
    const id = target.kind === "chat" ? backendSessionId! : target.id;
    loadTarget(kind, id).then(
      () => {
        if (!cancelled) setLoaded({ key: targetKey, storeKey: `${kind}:${id}`, failed: false });
      },
      () => {
        if (!cancelled) setLoaded({ key: targetKey, storeKey: null, failed: true });
      },
    );
    return () => {
      cancelled = true;
    };
    // `target` is rebuilt every render; its key names it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey, hasDraft, localChat, loadTarget, openChatDraft]);

  const current = loaded.key === targetKey ? loaded : null;
  const storeKey = hasDraft ? draftKey : localChat ? null : (current?.storeKey ?? null);
  const shownId = useNotesStore((s) => (storeKey === null ? undefined : s.byTarget[storeKey]));
  const noteId = shownId ?? null;
  // What the server knows the note by (export, the guide's tools): none
  // until its first write.
  const savedId = noteId !== null && !isUnsavedNote(noteId) ? noteId : null;

  // The note scrolls with the reader (Follow); the guide opens from the
  // toolbar's ⋯ menu and from the editor tip.
  const [panel, setPanel] = useState<HTMLElement | null>(null);
  const followLabel = useFollowReading({
    noteId,
    paperId: tab === "paper" ? paperId : null,
    root: panel,
  });
  const [guideOpen, setGuideOpen] = useState(false);
  // ... and its changed block is brought into view once the note shows.
  useEffect(() => {
    if (!request || !panel) return;
    // An idea's note has no tab here: the pane just opens. A chat's note
    // shows only for the chat on screen (a link to another chat switches
    // chats first).
    if (request.blockId === null || request.kind === "idea" || !requestShowsHere(request, backendSessionId)) {
      consumeRequest(request.seq);
      return;
    }
    // Never left waiting: a note that does not show (or a block that never
    // appears) drops the request after a while.
    const giveUp = setTimeout(() => consumeRequest(request.seq), REVEAL_GIVE_UP_MS);
    if (noteId !== request.noteId) return () => clearTimeout(giveUp);
    clearTimeout(giveUp);
    const ctrl = new AbortController();
    void revealBlock(panel, request.blockId, { unfold: unfoldInNote(noteId), signal: ctrl.signal }).then(() => {
      if (!ctrl.signal.aborted) consumeRequest(request.seq);
    });
    return () => ctrl.abort();
  }, [request, panel, noteId, backendSessionId, consumeRequest]);

  const sharedBy = useNotesStore((s) => (noteId === null ? null : (s.notes[noteId]?.sharedBy ?? null)));
  const { onExport, docxReason } = useNoteExport(savedId);

  return (
    <section
      aria-label={t("pane.label", "Notes")}
      className="@container flex h-full min-w-0 flex-col bg-card"
    >
      <NotesTabs
        active={tab}
        idBase={idBase}
        paperTitle={paperTitle}
        chatTitle={session?.title ?? ""}
        chatAvailable={session !== null}
        onSelect={setChosen}
        onClose={onClose}
      />
      <NotesToolbar
        noteId={noteId}
        followLabel={followLabel}
        followAvailable={tab === "paper"}
        sharedBy={sharedBy}
        onExport={savedId === null ? undefined : onExport}
        docxReason={docxReason}
        guideOpen={guideOpen}
        onGuideOpenChange={setGuideOpen}
      />
      <div
        ref={setPanel}
        id={notePanelId(idBase)}
        role="tabpanel"
        aria-labelledby={noteTabId(idBase, tab)}
        className="relative min-h-0 flex-1 overflow-x-clip overflow-y-auto"
      >
        {noteId !== null ? (
          <div
            key={storeKey ?? ""}
            className={cn(
              "h-full animate-in fade-in-0 duration-200 ease-out motion-reduce:animate-none",
              tab === "chat" ? "slide-in-from-right-3" : "slide-in-from-left-3",
            )}
          >
            <NoteEditor noteId={noteId} variant="pane" onOpenGuide={() => setGuideOpen(true)} />
          </div>
        ) : current?.failed ? (
          <p role="alert" className="px-4 py-3 text-sm text-muted-foreground">
            {t("pane.loadFailed", "Couldn't load this note.")}
          </p>
        ) : target === null ? (
          <p className="px-4 py-3 text-sm text-muted-foreground">
            {t("pane.empty", "Open a paper or start a chat to take notes.")}
          </p>
        ) : (
          <p
            role="status"
            className="px-4 py-3 text-sm text-muted-foreground animate-in fade-in-0 duration-200 motion-reduce:animate-none"
          >
            {t("loading", "Loading the note…")}
          </p>
        )}
      </div>
    </section>
  );
}
