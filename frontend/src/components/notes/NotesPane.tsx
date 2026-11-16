/** The Notes pane of the chat's right panel: the note of the paper in the
 * reader, or of this chat, in the full editor. */
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useNotesStore } from "@/store/notes";

import { NoteEditor } from "./NoteEditor";
import { notePanelId, noteTabId } from "./noteTabIds";
import { NotesTabs, type NoteTab } from "./NotesTabs";

interface Props {
  onClose: () => void;
}

type Target = { kind: "paper" | "session"; id: number };

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
  const tab: NoteTab = paperId === null ? "chat" : session === null ? "paper" : chosen;
  const target: Target | null =
    tab === "paper" && paperId !== null
      ? { kind: "paper", id: paperId }
      : backendSessionId !== null
        ? { kind: "session", id: backendSessionId }
        : null;

  const refTitle = useChatStore((s) =>
    backendSessionId === null || paperId === null
      ? undefined
      : s.referencesBySession[backendSessionId]?.find((r) => r.paper_content_id === paperId)?.title,
  );
  const paperNoteTitle = useNotesStore((s) => {
    const id = paperId === null ? undefined : s.byTarget[`paper:${paperId}`];
    return id === undefined ? undefined : s.notes[id]?.note.title;
  });
  const paperTitle =
    paperId === null ? null : (refTitle ?? paperNoteTitle ?? t("pane.paper", "Paper"));

  const [loaded, setLoaded] = useState<{ key: string; noteId: number | null; failed: boolean }>({
    key: "",
    noteId: null,
    failed: false,
  });
  const targetKey = target ? `${target.kind}:${target.id}` : "";
  const loadTarget = useNotesStore((s) => s.loadTarget);
  const ensureBackendSession = useChatStore((s) => s.ensureBackendSession);

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    loadTarget(target.kind, target.id).then(
      (noteId) => {
        if (!cancelled) setLoaded({ key: targetKey, noteId, failed: false });
      },
      () => {
        if (!cancelled) setLoaded({ key: targetKey, noteId: null, failed: true });
      },
    );
    return () => {
      cancelled = true;
    };
    // `target` is rebuilt every render; its key names it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey, loadTarget]);

  // A chat not yet sent to the server gets its session when its note is asked for.
  const needsSession = tab === "chat" && session !== null && backendSessionId === null;
  useEffect(() => {
    if (needsSession && activeSessionId !== null) {
      void ensureBackendSession(activeSessionId).catch(() => {
        setLoaded({ key: "", noteId: null, failed: true });
      });
    }
  }, [needsSession, activeSessionId, ensureBackendSession]);

  const current = loaded.key === targetKey ? loaded : null;

  return (
    <section
      aria-label={t("pane.label", "Notes")}
      className="flex h-full min-w-0 flex-col bg-card"
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
      {/* Task 17 fills this row with the note's toolbar. */}
      <div data-notes-toolbar />
      <div
        id={notePanelId(idBase)}
        role="tabpanel"
        aria-labelledby={noteTabId(idBase, tab)}
        className="relative min-h-0 flex-1 overflow-y-auto"
      >
        {current?.noteId != null ? (
          <div
            key={current.noteId}
            className={cn(
              "h-full animate-in fade-in-0 duration-200 ease-out motion-reduce:animate-none",
              tab === "chat" ? "slide-in-from-right-3" : "slide-in-from-left-3",
            )}
          >
            <NoteEditor noteId={current.noteId} variant="pane" />
          </div>
        ) : current?.failed ? (
          <p role="alert" className="px-4 py-3 text-sm text-muted-foreground">
            {t("pane.loadFailed", "Couldn't load this note.")}
          </p>
        ) : target === null && !needsSession ? (
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
