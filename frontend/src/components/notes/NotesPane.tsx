/** The Notes pane of the chat's right panel: the note of the paper in the
 * reader, or of this chat, in the full editor. */
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { REVEAL_GIVE_UP_MS, revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { addPaperToChat, neighbourOf } from "@/lib/paperTabs";
import { cn } from "@/lib/utils";
import { useCanvasStore, type OutsideTab } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useNotesPaneStore } from "@/store/notesPane";
import { isUnsavedNote, noteHasContent, useNotesStore } from "@/store/notes";

import { NoteEditor } from "./NoteEditor";
import { isFindKey } from "./noteFind";
import { notePanelId, noteTabId } from "./noteTabIds";
import { NotesTabs, type NoteTab } from "./NotesTabs";
import { NotesToolbar } from "./NotesToolbar";
import { TocRail } from "./TocRail";
import { useFollowReading } from "./useFollowReading";
import { useNoteExport } from "./useNoteExport";

interface Props {
  onClose: () => void;
}

/** Stable "no dashed tabs" (keeps the selector's result stable). */
const NO_OUTSIDE: OutsideTab[] = [];

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

  // The paper the reader shows is the paper tab, and stays it after the
  // reader closes; a paper opened from an agent's change card comes forward
  // at once (the reader catches up after). `reader`: the reader's paper last
  // seen, so a change in it (not a mere difference) wins.
  const refs = useChatStore((s) =>
    backendSessionId === null ? undefined : s.referencesBySession[backendSessionId],
  );
  const [chosen, setChosen] = useState<NoteTab>("paper");
  // A chat starts on the paper's tab: a tab choice belongs to one chat.
  const [chosenFor, setChosenFor] = useState(activeSessionId);
  if (chosenFor !== activeSessionId) {
    setChosenFor(activeSessionId);
    setChosen("paper");
  }
  const [paper, setPaper] = useState({ id: readerPaperId, session: activeSessionId, reader: readerPaperId });
  if (paper.session !== activeSessionId) {
    // Entering a chat: the reader may still show the chat left behind (it
    // closes after the switch), so its paper is taken only when it is one
    // of this chat's papers; papers not loaded yet are not known to be.
    const ours = readerPaperId !== null && refs?.some((r) => r.paper_content_id === readerPaperId) === true;
    setPaper({ id: ours ? readerPaperId : null, session: activeSessionId, reader: readerPaperId });
  } else if (readerPaperId !== paper.reader) {
    setPaper({ id: readerPaperId ?? paper.id, session: activeSessionId, reader: readerPaperId });
    // The reader switched papers: the notes switch with it (off This chat
    // too); the reader merely opening or closing leaves the tab alone.
    if (readerPaperId !== null && paper.reader !== null) setChosen("paper");
  }
  // A chat whose reader has shown no paper yet opens on the paper whose
  // note was last on screen in it, else its first paper (Obsidian / Notion
  // reopen the last page): never an empty pane while the chat has papers.
  const lastViewed = useNotesPaneStore((s) =>
    activeSessionId === null ? undefined : s.lastPaperByChat[activeSessionId],
  );
  const enabledRefs = refs?.filter((r) => r.enabled);
  const fallbackPaperId =
    lastViewed !== undefined &&
    (enabledRefs === undefined || enabledRefs.some((r) => r.paper_content_id === lastViewed))
      ? lastViewed
      : (enabledRefs?.[0]?.paper_content_id ?? null);
  const paperId = paper.id ?? fallbackPaperId;
  // A dashed tab closed (here or in the reader) while its note is on
  // screen: the pane lands on the neighbouring paper.
  const closedTab = useCanvasStore((s) => s.closedTab);
  const [seenClosed, setSeenClosed] = useState(closedTab?.n ?? 0);
  if (closedTab && closedTab.n !== seenClosed) {
    setSeenClosed(closedTab.n);
    if (closedTab.chat === activeSessionId && paperId === closedTab.paperId) {
      const neighbour = closedTab.neighbour;
      setPaper((p) => ({ ...p, id: neighbour }));
    }
  }
  // "Open in notes" (the agent's change card): the note's tab comes forward.
  const request = useNotesPaneStore((s) => s.request);
  const consumeRequest = useNotesPaneStore((s) => s.consumeRequest);
  const [seenRequest, setSeenRequest] = useState(0);
  if (request && request.seq !== seenRequest) {
    setSeenRequest(request.seq);
    if (request.kind === "session") {
      if (requestShowsHere(request, backendSessionId)) setChosen("chat");
    }
    else if (request.kind === "paper") {
      setChosen("paper");
      const id = request.id;
      setPaper((p) => ({ ...p, id }));
    }
  }
  // The chat's note earns its tab once it has content (a block with text,
  // or a title the user set). Its state is kept up to date here even while
  // the paper's note is on screen.
  const draftKey = activeSessionId === null ? null : `local:${activeSessionId}`;
  const chatNoteId = useNotesStore((s) => {
    const draft = draftKey === null ? undefined : s.byTarget[draftKey];
    return draft ?? (backendSessionId === null ? undefined : s.byTarget[`session:${backendSessionId}`]);
  });
  const chatHasContent = useNotesStore((s) =>
    chatNoteId === undefined ? false : noteHasContent(s.notes[chatNoteId]),
  );
  // Once shown, the chat tab stays while it is the selected tab, even if
  // its note empties (never move the user off the page they type in); it
  // is re-evaluated on switching chats or tabs.
  const chatSelected = paperId === null || chosen === "chat";
  const [pin, setPin] = useState<number | null>(null);
  // (Adjusted while rendering, the React way to derive state from props:
  // an effect would lag a frame and the lint rule forbids it.)
  if (pin !== null && (pin !== activeSessionId || !chatSelected)) setPin(null);
  else if (pin === null && chatHasContent && chatSelected && activeSessionId !== null) setPin(activeSessionId);
  // The tab animates in only for a note seen empty (or missing) first.
  const [seenEmpty, setSeenEmpty] = useState<number | null>(null);
  if (seenEmpty !== activeSessionId) {
    if (chatNoteId !== undefined && !chatHasContent) setSeenEmpty(activeSessionId);
    else if (seenEmpty !== null) setSeenEmpty(null);
  }
  const chatShown = session !== null && (chatHasContent || (pin !== null && pin === activeSessionId && chatSelected));
  const tab: NoteTab = !chatShown ? (paperId === null ? "chat" : "paper") : paperId === null ? "chat" : chosen;
  // The paper whose note is on screen is the one this chat reopens on.
  const rememberPaper = useNotesPaneStore((s) => s.rememberPaper);
  useEffect(() => {
    if (tab === "paper" && paperId !== null && activeSessionId !== null) rememberPaper(activeSessionId, paperId);
  }, [tab, paperId, activeSessionId, rememberPaper]);
  // The chat tab names the chat by its local id: it stays the same note
  // when the chat reaches the server (its first message, or the note's
  // first write).
  const target: Target | null =
    tab === "paper" && paperId !== null
      ? { kind: "paper", id: paperId }
      : chatShown && activeSessionId !== null
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

  // The paper tabs: the same set the reader shows (the chat's papers, then
  // a dashed tab for a paper shown but not in the chat).
  const outsideTabs = useCanvasStore((s) =>
    activeSessionId === null ? NO_OUTSIDE : (s.outsideByChat[activeSessionId] ?? NO_OUTSIDE),
  );
  const paperTitles = useCanvasStore((s) => s.paperTitles);
  const enabledIds = (enabledRefs ?? []).map((r) => r.paper_content_id);
  // Papers not loaded yet: the paper on screen still has its tab.
  const paperTabs =
    enabledRefs === undefined
      ? paperId !== null && paperTitle !== null
        ? [{ id: paperId, title: paperTitle }]
        : []
      : enabledRefs.map((r) => ({ id: r.paper_content_id, title: r.title }));
  const outsideItems = outsideTabs
    .filter((o) => !enabledIds.includes(o.paperId))
    .map((o) => ({
      id: o.paperId,
      preview: o.preview,
      title:
        refs?.find((r) => r.paper_content_id === o.paperId)?.title ??
        paperTitles[o.paperId] ??
        (o.paperId === paperId ? paperNoteTitle : undefined) ??
        t("pane.paper", "Paper"),
    }));
  const tabOrder = [...paperTabs.map((p) => p.id), ...outsideItems.map((o) => o.id)];
  const enabledKey = enabledRefs === undefined ? null : enabledIds.join(",");
  // A paper on screen here that is not one of the chat's (a switched-off
  // paper, a note opened from elsewhere) takes a dashed tab, as in the reader.
  useEffect(() => {
    if (activeSessionId === null || tab !== "paper" || paperId === null || enabledKey === null) return;
    if (enabledKey.split(",").includes(String(paperId)) || outsideTabs.some((o) => o.paperId === paperId)) return;
    useCanvasStore.getState().showOutside(activeSessionId, paperId);
  }, [activeSessionId, tab, paperId, enabledKey, outsideTabs]);
  const selectPaperTab = (id: number) => {
    setChosen("paper");
    setPaper((p) => ({ ...p, id }));
    // Both panes show the same paper: the reader follows (closed, it shows
    // the paper when it opens).
    if (useCanvasStore.getState().activePaperId !== id) useCanvasStore.getState().selectPaper(id);
  };
  const addPaperTab = async (id: number) => {
    const chat = activeSessionId;
    if (backendSessionId === null || chat === null) return;
    if (await addPaperToChat(backendSessionId, id)) useCanvasStore.getState().forgetOutside(chat, id);
  };
  const closePaperTab = (id: number) => {
    if (activeSessionId === null) return;
    useCanvasStore.getState().closeOutside(activeSessionId, id, neighbourOf(tabOrder, id));
  };
  const keepPaperTab = (id: number) => {
    if (activeSessionId !== null) useCanvasStore.getState().keepOutside(activeSessionId, id);
  };

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
  // The chat whose note lookup failed (a failure belongs to its chat).
  const [probeFailedFor, setProbeFailedFor] = useState<number | null>(null);
  const probeFailed = probeFailedFor !== null && probeFailedFor === activeSessionId;
  const hasDraft = useNotesStore((s) => draftKey !== null && s.byTarget[draftKey] !== undefined);
  const localChat = target?.kind === "chat" && backendSessionId === null;

  // Looks at the chat's note while the paper's shows: its tab needs to
  // know whether it has content (an empty look creates nothing).
  useEffect(() => {
    if (session === null || activeSessionId === null || target?.kind === "chat") return;
    if (backendSessionId === null) {
      openChatDraft(activeSessionId);
      return;
    }
    if (hasDraft) return;
    loadTarget("session", backendSessionId).catch(() => setProbeFailedFor(activeSessionId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId, backendSessionId, session === null, target?.kind, hasDraft, loadTarget, openChatDraft]);

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

  // The note slides in from the side of the tab picked (This chat is last).
  const tabPos = tab === "chat" ? tabOrder.length : paperId === null ? -1 : tabOrder.indexOf(paperId);
  const [slideFrom, setSlideFrom] = useState({ key: targetKey, session: activeSessionId, pos: tabPos, dir: 0 });
  if (slideFrom.key !== targetKey) {
    const same = slideFrom.session === activeSessionId && tabPos >= 0 && slideFrom.pos >= 0;
    setSlideFrom({ key: targetKey, session: activeSessionId, pos: tabPos, dir: same ? Math.sign(tabPos - slideFrom.pos) : 0 });
  }

  const current = loaded.key === targetKey ? loaded : null;
  const storeKey = hasDraft && target?.kind === "chat" ? draftKey : localChat ? null : (current?.storeKey ?? null);
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
  // Find: the toolbar's button or Ctrl+F anywhere in the pane opens the
  // note's find strip, which the editor puts in the host under the toolbar.
  const [findHost, setFindHost] = useState<HTMLElement | null>(null);
  const [findRequest, setFindRequest] = useState(0);
  const requestFind = () => setFindRequest((n) => n + 1);
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

  // Editing a temporary paper's note keeps its tab (the note belongs to the
  // paper in the library, not to the chat: the paper is not added).
  const previewPaper =
    tab === "paper" && paperId !== null && outsideTabs.some((o) => o.paperId === paperId && o.preview)
      ? paperId
      : null;
  useEffect(() => {
    if (!panel || previewPaper === null || activeSessionId === null) return;
    const chat = activeSessionId;
    const keep = () => useCanvasStore.getState().keepOutside(chat, previewPaper);
    const events = ["beforeinput", "paste", "drop"] as const;
    for (const e of events) panel.addEventListener(e, keep);
    return () => {
      for (const e of events) panel.removeEventListener(e, keep);
    };
  }, [panel, previewPaper, activeSessionId]);

  const sharedBy = useNotesStore((s) => (noteId === null ? null : (s.notes[noteId]?.sharedBy ?? null)));
  const { onExport, docxReason } = useNoteExport(savedId);

  return (
    <section
      aria-label={t("pane.label", "Notes")}
      className="notes-pane @container flex h-full min-w-0 flex-col bg-card"
      onKeyDownCapture={(e) => {
        if (noteId === null || !isFindKey(e)) return;
        e.preventDefault();
        e.stopPropagation();
        requestFind();
      }}
    >
      <NotesTabs
        active={tab}
        idBase={idBase}
        papers={paperTabs}
        outside={outsideItems}
        paperId={paperId}
        chatTitle={session?.title ?? ""}
        chatAvailable={chatShown}
        animateChatIn={seenEmpty !== null && seenEmpty === activeSessionId}
        onSelectPaper={selectPaperTab}
        onAddPaper={(id) => void addPaperTab(id)}
        onClosePaper={closePaperTab}
        onKeepPaper={keepPaperTab}
        onSelectChat={() => setChosen("chat")}
        onClose={onClose}
        resetKey={activeSessionId}
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
        onFind={noteId === null ? undefined : requestFind}
      />
      {/* The find strip, full width directly under the toolbar. */}
      <div ref={setFindHost} data-find-host="notes-pane" className="flex shrink-0 flex-col" />
      <div
        ref={setPanel}
        id={notePanelId(idBase)}
        role="tabpanel"
        aria-labelledby={noteTabId(idBase, tab === "paper" && paperId !== null ? `paper-${paperId}` : "chat")}
        // Clipped, never scrolled: the note editor inside scrolls itself. A
        // scrolling pane grew a second scrollbar whenever a floating layer
        // (BlockNote's side menu, left at an old block during a fast
        // scroll) reached past its bottom.
        className="relative min-h-0 flex-1 overflow-clip"
      >
        {/* The table of contents floats at the pane's right edge: a
            zero-height sticky layer, so it stays put and takes no width. */}
        <div data-toc-layer className="sticky top-0 z-20 h-0">
          <TocRail noteId={noteId} root={panel} />
        </div>
        {noteId !== null ? (
          <div
            key={storeKey ?? ""}
            className={cn(
              "h-full animate-in fade-in-0 duration-200 ease-out motion-reduce:animate-none",
              slideFrom.dir > 0 && "slide-in-from-right-3",
              slideFrom.dir < 0 && "slide-in-from-left-3",
            )}
          >
            <NoteEditor
              noteId={noteId}
              variant="pane"
              onOpenGuide={() => setGuideOpen(true)}
              findHost={findHost}
              findRequest={findRequest}
            />
          </div>
        ) : current?.failed ? (
          <p role="alert" className="px-4 py-3 text-sm text-muted-foreground">
            {t("pane.loadFailed", "Couldn't load this note.")}
          </p>
        ) : target === null && session !== null && chatNoteId === undefined && !probeFailed ? (
          // The chat's note is still being looked up: no empty state yet.
          <p
            role="status"
            className="px-4 py-3 text-sm text-muted-foreground animate-in fade-in-0 duration-200 motion-reduce:animate-none"
          >
            {t("loading", "Loading the note…")}
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
