import { Suspense, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ChatThread } from "@/components/chat/ChatThread";
import { SENT_SEARCH_PREFIX } from "@/components/chat/MessageBubble";
import { Composer } from "@/components/chat/Composer";
import { AutoApproveToggle } from "@/components/chat/AutoApproveToggle";
import { RightPanel } from "@/components/chat/RightPanel";
import { PanelLoadBoundary } from "@/components/states/PanelLoadBoundary";
import { retryableLazy } from "@/lib/retryableLazy";
import { slideStageLabel } from "@/lib/slideStage";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { useSlidesStore } from "@/store/slides";
import { useNotesPaneStore } from "@/store/notesPane";
import { useSettingsStore } from "@/store/settings";
import { hasBlockingConfigIssue } from "@/lib/readiness";
import { useGlobalShortcuts } from "@/hooks/useGlobalShortcuts";
import { useReferencesSync } from "@/hooks/useReferencesSync";
import { useSessionsSync } from "@/hooks/useSessionsSync";
import { useDeckSync } from "@/hooks/useDeckSync";
import { useCloseCanvasOnSessionChange } from "@/hooks/useCloseCanvasOnSessionChange";
import { useRightPanel } from "@/hooks/useRightPanel";
import { useRunReattach } from "@/hooks/useRunReattach";
import { getDeck, updateDeckNote } from "@/lib/api";
import { cn } from "@/lib/utils";

const CitationCanvas = retryableLazy(() =>
  import("@/components/canvas/CitationCanvas").then((m) => ({
    default: m.CitationCanvas,
  })),
);

const MemoryManager = retryableLazy(() =>
  import("@/components/chat/MemoryManager").then((m) => ({
    default: m.MemoryManager,
  })),
);

const SlidesPanel = retryableLazy(() =>
  import("@/components/slides/SlidesPanel").then((m) => ({
    default: m.SlidesPanel,
  })),
);

export function ChatPage() {
  const { t } = useTranslation("chat");
  useGlobalShortcuts();
  useSessionsSync();
  useReferencesSync();
  useDeckSync();
  useRunReattach();
  const canvasOpen = useCanvasStore((s) => s.open);
  const toggleCanvas = useCanvasStore((s) => s.toggleCanvas);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const newSession = useChatStore((s) => s.newSession);
  const { send, stop } = useChatStream();

  const slidesOpen = useSlidesStore((s) => s.open);
  const slidesEverOpened = useSlidesStore((s) => s.everOpened);
  const openSlides = useSlidesStore((s) => s.openPanel);
  const closeSlides = useSlidesStore((s) => s.closePanel);

  const notesOpen = useNotesPaneStore((s) => s.open);
  const toggleNotes = useNotesPaneStore((s) => s.toggle);
  const closeNotes = useNotesPaneStore((s) => s.close);

  const [memoryOpen, setMemoryOpen] = useState(false);
  const [speakerNotes, setSpeakerNotes] = useState<Record<string, string>>({});

  // Close the canvas when the user switches chat sessions (it shows the
  // previous session's references).
  useCloseCanvasOnSessionChange(activeSessionId);

  // Fix 1: whenever the Canvas opens (via References button, openCitation, or
  // any other path) ensure Memory and Slides are closed. Uses Zustand's
  // subscribe API (calling setState in a subscription callback — not
  // synchronously in the effect body — satisfies the
  // react-hooks/set-state-in-effect rule). This covers the `openCitation`
  // path which sets open=true on the store directly, bypassing handleToggleCanvas.
  // Notes stays beside it; a newly opened reader or a citation brings the
  // Reader tab forward when the two share the panel as tabs (a paper picked
  // in the notes pane's own tabs does not: the user stays in Notes).
  useEffect(() => {
    return useCanvasStore.subscribe((state, prev) => {
      if (state.open) {
        setMemoryOpen(false);
        useSlidesStore.getState().closePanel();
        if (
          !prev.open ||
          state.requestNonce !== prev.requestNonce ||
          (state.paperRequestNonce !== prev.paperRequestNonce && !state.paperRequestQuiet)
        ) {
          useNotesPaneStore.getState().setActiveTab("reader");
        }
      }
    });
  }, []);

  // When Slides opens, close Canvas and Memory (Notes stays beside it).
  useEffect(() => {
    return useSlidesStore.subscribe((state, prev) => {
      if (state.open) {
        setMemoryOpen(false);
        useCanvasStore.getState().closeCanvas();
        if (!prev.open) useNotesPaneStore.getState().setActiveTab("reader");
      }
    });
  }, []);

  // Memory stays alone: Notes opening (from any path) closes it.
  useEffect(() => {
    return useNotesPaneStore.subscribe((state) => {
      if (state.open) setMemoryOpen(false);
    });
  }, []);

  // Fix 3: close Memory and Slides on session switch. Memory content is
  // per-session. Uses Zustand's subscribe API (same approach as Fix 1) so
  // setState is called in a subscription callback rather than synchronously in
  // the effect body, satisfying the react-hooks/set-state-in-effect rule.
  const prevSessionForMemoryRef = useRef(
    useChatStore.getState().activeSessionId,
  );
  useEffect(() => {
    return useChatStore.subscribe((state) => {
      if (prevSessionForMemoryRef.current !== state.activeSessionId) {
        prevSessionForMemoryRef.current = state.activeSessionId;
        setMemoryOpen(false);
        useSlidesStore.getState().closePanel();
      }
    });
  }, []);

  const activeSession =
    activeSessionId === null
      ? null
      : (sessions.find((s) => s.id === activeSessionId) ?? null);

  const backendSessionId = activeSession?.backend_session_id ?? null;

  const isStreaming =
    activeSession?.messages.some(
      (m) => m.status === "streaming" || m.status === "processing",
    ) ?? false;

  // First-run gate: lock the composer only on a definitive config problem
  // (missing / rejected key) — NOT on a transient readiness blip (e.g. the
  // re-ping after the site idled). `null` (not yet probed) stays unlocked to
  // avoid a lock-flash for configured users.
  const setupRequired = useSettingsStore(
    (s) => s.readiness != null && hasBlockingConfigIssue(s.readiness),
  );
  const openSettings = useSettingsStore((s) => s.open);

  const deckForChip = useSlidesStore((s) =>
    backendSessionId === null ? undefined : s.deckBySession[backendSessionId]);
  const currentPageForChip = useSlidesStore((s) =>
    backendSessionId === null ? 1 : (s.currentPageBySession[backendSessionId] ?? 1));
  const slideAttached = useSlidesStore((s) =>
    backendSessionId === null ? true
      : (s.slideAttachedBySession[backendSessionId] ?? true));
  const setSlideAttached = useSlidesStore((s) => s.setSlideAttached);

  const slideChip =
    backendSessionId !== null && deckForChip && slidesOpen
      ? {
          page: currentPageForChip,
          attached: slideAttached,
          onToggle: () => setSlideAttached(backendSessionId, !slideAttached),
        }
      : null;

  // A slides generate/edit turn in flight for the active session: drives the
  // Slides-panel editing mask (mask + hold the current deck) and the
  // reload-on-complete. The streaming message's trace gives the live stage.
  const slidesTurn = activeSession?.messages.find(
    (m) => m.status === "streaming" && m.routing_decision?.intent === "slides",
  );
  const deckBusy = slidesTurn !== undefined;
  const deckStage = deckBusy ? slideStageLabel(slidesTurn?.trace) : undefined;
  // Revision for the active deck — bumps on each `deck` event so the notes
  // refetch (below) and the panel's PDF refetch pick up a completed edit.
  const deckRevision = useSlidesStore((s) =>
    backendSessionId === null
      ? 0
      : (s.deckRevisionBySession[backendSessionId] ?? 0),
  );

  // The right column is shared between the reader (Citation Canvas), Slides,
  // Memory and Notes; Reader or Slides pair with Notes, Memory stays alone.
  const panel = useRightPanel(memoryOpen);
  const { behindTab, setActiveTab } = panel;

  // Keep the Slides panel MOUNTED once it has been opened (store `everOpened`
  // latch), so swapping to the Citation Canvas and back doesn't unmount it — an
  // unmount throws away the parsed+rasterized PDF, so reopening would refetch
  // and re-render the whole deck (the ~1s "loading" flash on every swap). The
  // panel then just toggles `hidden`, preserving its rendered pages (mirrors
  // the always-mounted CitationCanvas above).
  const slidesMounted = slidesEverOpened && backendSessionId !== null;

  // Fetch speaker notes when the Slides panel opens and a backend session
  // exists. Resets when the session changes.
  useEffect(() => {
    if (!slidesOpen || backendSessionId === null) return;
    // While an edit is in flight, getDeck would return the pre-edit notes;
    // hold until it completes. deckRevision bumps on completion → refetch.
    if (deckBusy) return;
    let cancelled = false;
    getDeck(backendSessionId)
      .then((d) => {
        if (!cancelled) setSpeakerNotes(d.speaker_notes);
      })
      .catch(() => {
        if (!cancelled) setSpeakerNotes({});
      });
    return () => {
      cancelled = true;
    };
  }, [slidesOpen, backendSessionId, deckBusy, deckRevision]);

  // Persist a manual speaker-note edit, then refresh the local notes map from
  // the backend's rebuilt response so the pane shows the saved text.
  const handleSaveNote = async (page: number, text: string): Promise<void> => {
    if (backendSessionId === null) return;
    try {
      const res = await updateDeckNote(backendSessionId, page, text);
      setSpeakerNotes(res.speaker_notes);
    } catch (err: unknown) {
      toast.error(t("toast.saveNoteFailed"), {
        description: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }
  };

  const handleSubmit = (text: string): void => {
    const sessionId = activeSessionId ?? newSession();
    send(sessionId, text).catch((err: unknown) => {
      toast.error(t("toast.requestFailed"), {
        description: err instanceof Error ? err.message : String(err),
      });
    });
  };

  // H5a: run an approved held search as a follow-up turn. Errors (a 409 when
  // the approval was already used) are handled by the card.
  const handleRunHeld = (heldId: string, text: string): Promise<void> => {
    const sessionId = activeSessionId ?? newSession();
    return send(sessionId, "", {
      approvedHeldId: heldId,
      userBubble: `${SENT_SEARCH_PREFIX}${text}`,
    });
  };

  const handleToggleMemory = (): void => {
    // Always allowed: with no backend session yet, the panel shows global
    // (user) memories only (project/session memories need a sent message).
    if (!memoryOpen) {
      // Opening Memory → close Canvas + Slides + Notes if they were open.
      closeCanvas();
      closeSlides();
      closeNotes();
      setMemoryOpen(true);
    } else {
      setMemoryOpen(false);
    }
  };

  // A pane hidden behind the other tab comes forward instead of closing.
  const handleToggleCanvas = (): void => {
    if (canvasOpen && behindTab("reader")) {
      setActiveTab("reader");
      return;
    }
    if (!canvasOpen) {
      // Opening Canvas → close Memory + Slides if they were open.
      setMemoryOpen(false);
      closeSlides();
    }
    toggleCanvas();
  };

  const handleToggleSlides = (): void => {
    if (slidesOpen && behindTab("reader")) {
      setActiveTab("reader");
      return;
    }
    if (!slidesOpen) {
      // Opening Slides → close Canvas + Memory if they were open.
      closeCanvas();
      setMemoryOpen(false);
      openSlides();
    } else {
      closeSlides();
    }
  };

  const handleToggleNotes = (): void => {
    if (notesOpen && behindTab("notes")) {
      setActiveTab("notes");
      return;
    }
    toggleNotes();
  };

  return (
    <div
      className={cn(
        "grid flex-1 min-h-0",
        // No width transition while dragging the divider, so it tracks the cursor.
        !panel.dragging && "transition-[grid-template-columns] duration-200",
      )}
      style={{ gridTemplateColumns: `1fr ${panel.open ? panel.width : 0}px` }}
    >
      <div className="flex min-h-0 min-w-0 flex-col">
        <ChatThread
          session={activeSession}
          onSend={handleSubmit}
          onSendHeld={handleRunHeld}
        />
        <Composer
          onSubmit={handleSubmit}
          disabled={isStreaming || setupRequired}
          isStreaming={isStreaming}
          onStop={stop}
          setupRequired={setupRequired}
          onOpenSettings={openSettings}
          memoryOpen={memoryOpen}
          onToggleMemory={handleToggleMemory}
          onToggleCanvas={handleToggleCanvas}
          canvasOpen={canvasOpen}
          slidesOpen={slidesOpen}
          onToggleSlides={handleToggleSlides}
          notesOpen={notesOpen}
          onToggleNotes={handleToggleNotes}
          slideChip={slideChip}
          actions={<AutoApproveToggle />}
        />
      </div>
      {/* Right panel — shared slot for the reader, Slides, Memory and Notes.
          Stays mounted (collapsed to 0 width when closed) so kept-alive paper
          iframes survive open/close cycles without re-rendering. */}
      <RightPanel
        panel={panel}
        resizeLabel={t("panel.resize")}
        memoryLabel={t("panel.memory")}
        slidesLabel={t("panel.slides")}
        reader={
          <PanelLoadBoundary onRetry={CitationCanvas.reset}>
            <Suspense fallback={null}>
              <CitationCanvas />
            </Suspense>
          </PanelLoadBoundary>
        }
        memory={
          <PanelLoadBoundary onRetry={MemoryManager.reset}>
            <Suspense fallback={null}>
              <MemoryManager sessionId={backendSessionId} />
            </Suspense>
          </PanelLoadBoundary>
        }
        slides={
          slidesMounted && backendSessionId !== null ? (
            <PanelLoadBoundary onRetry={SlidesPanel.reset}>
              <Suspense fallback={null}>
                <SlidesPanel
                  sessionId={backendSessionId}
                  speakerNotes={speakerNotes}
                  busy={deckBusy}
                  stage={deckStage}
                  onSaveNote={handleSaveNote}
                />
              </Suspense>
            </PanelLoadBoundary>
          ) : null
        }
      />
    </div>
  );
}
