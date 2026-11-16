import { Suspense, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ChatThread } from "@/components/chat/ChatThread";
import { SENT_SEARCH_PREFIX } from "@/components/chat/MessageBubble";
import { Composer } from "@/components/chat/Composer";
import { AutoApproveToggle } from "@/components/chat/AutoApproveToggle";
import { PanelLoadBoundary } from "@/components/states/PanelLoadBoundary";
import { retryableLazy } from "@/lib/retryableLazy";
import { slideStageLabel } from "@/lib/slideStage";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { useSlidesStore } from "@/store/slides";
import { rightPanelLayout, useNotesPaneStore, type PanelLayout } from "@/store/notesPane";
import { useSettingsStore } from "@/store/settings";
import { hasBlockingConfigIssue } from "@/lib/readiness";
import { useGlobalShortcuts } from "@/hooks/useGlobalShortcuts";
import { useReferencesSync } from "@/hooks/useReferencesSync";
import { useSessionsSync } from "@/hooks/useSessionsSync";
import { useDeckSync } from "@/hooks/useDeckSync";
import { useCloseCanvasOnSessionChange } from "@/hooks/useCloseCanvasOnSessionChange";
import { useCanvasResize, useNotesDividerResize } from "@/hooks/useCanvasResize";
import { usePresence } from "@/hooks/usePresence";
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

const NotesPane = retryableLazy(() =>
  import("@/components/notes/NotesPane").then((m) => ({
    default: m.NotesPane,
  })),
);

/** Exit time of a pane leaving the right panel (ms). */
const PANE_EXIT_MS = 200;

const SlidesPanel = retryableLazy(() =>
  import("@/components/slides/SlidesPanel").then((m) => ({
    default: m.SlidesPanel,
  })),
);

export function ChatPage() {
  const { t } = useTranslation(["chat", "notes"]);
  useGlobalShortcuts();
  useSessionsSync();
  useReferencesSync();
  useDeckSync();
  useRunReattach();
  const canvasOpen = useCanvasStore((s) => s.open);
  const toggleCanvas = useCanvasStore((s) => s.toggleCanvas);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const { width: canvasWidth, resizing, onPointerDown } = useCanvasResize();
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const newSession = useChatStore((s) => s.newSession);
  const { send, stop } = useChatStream();

  const slidesOpen = useSlidesStore((s) => s.open);
  const slidesEverOpened = useSlidesStore((s) => s.everOpened);
  const openSlides = useSlidesStore((s) => s.openPanel);
  const closeSlides = useSlidesStore((s) => s.closePanel);

  const notesOpen = useNotesPaneStore((s) => s.open);
  const activeTab = useNotesPaneStore((s) => s.activeTab);
  const storedNotesWidth = useNotesPaneStore((s) => s.notesWidth);
  const setActiveTab = useNotesPaneStore((s) => s.setActiveTab);
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
  // Reader tab forward when the two share the panel as tabs.
  useEffect(() => {
    return useCanvasStore.subscribe((state, prev) => {
      if (state.open) {
        setMemoryOpen(false);
        useSlidesStore.getState().closePanel();
        if (
          !prev.open ||
          state.requestNonce !== prev.requestNonce ||
          state.paperRequestNonce !== prev.paperRequestNonce
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

  // The right column is shared between the Citation Canvas, Memory Manager,
  // and Slides panel. Opening one closes the others; the column width + slide
  // animation is the same for all.
  const rightPanelOpen = canvasOpen || memoryOpen || slidesOpen || notesOpen;

  // Reader or Slides share the panel with Notes: tabs while narrow, side by
  // side from SIDE_BY_SIDE_MIN. Memory never shares it.
  const primaryOpen = canvasOpen || slidesOpen;
  const { layout, notesWidth } = rightPanelLayout({
    primaryOpen,
    notesOpen,
    panelWidth: canvasWidth,
    notesWidth: storedNotesWidth,
  });
  const divider = useNotesDividerResize(canvasWidth);
  const dragging = resizing || divider.resizing;

  // Panes leave by sliding out (never by animating width): each stays shown
  // for its exit. The panel column collapses around them when all close.
  const notesPresence = usePresence(notesOpen, PANE_EXIT_MS);
  const canvasPresence = usePresence(canvasOpen, PANE_EXIT_MS);
  const slidesPresence = usePresence(slidesOpen, PANE_EXIT_MS);
  const primaryPresence = usePresence(primaryOpen, PANE_EXIT_MS);
  // A swap shows the incoming pane at once; only a pane closing alone lingers.
  const canvasShown = canvasOpen || (canvasPresence.mounted && !slidesOpen && !memoryOpen);
  const slidesShown = slidesOpen || (slidesPresence.mounted && !canvasOpen && !memoryOpen);
  // The layout the two had together, so a closing pane leaves from where it sat.
  const [sharedLayout, setSharedLayout] = useState<PanelLayout>(layout);
  if (notesOpen && primaryOpen && layout !== sharedLayout) setSharedLayout(layout);
  const notesLeaving = !notesOpen && notesPresence.mounted;
  const primaryLeaving = !primaryOpen && primaryPresence.mounted && notesOpen;

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
  const behindTab = (tab: "reader" | "notes") => layout === "tabs" && activeTab !== tab;

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

  // Tabs stack both panes and slide the hidden one aside, toward its tab;
  // side by side, Notes keeps its width beside the reader.
  const hideProps = (hide: boolean) =>
    hide ? { inert: true, "aria-hidden": true as const } : {};
  const PANE_MOTION =
    "transition-[opacity,transform] duration-200 ease-out motion-reduce:transition-none";
  const LEAVE = "animate-out fade-out-0 duration-200 fill-mode-forwards motion-reduce:animate-none";

  const primaryClass = cn(
    "h-full min-w-0",
    primaryLeaving
      ? cn("absolute inset-y-0 left-0 z-[1] slide-out-to-left-8", LEAVE)
      : layout === "tabs"
        ? cn("absolute inset-0", PANE_MOTION, behindTab("reader") && "pointer-events-none -translate-x-3 opacity-0")
        : "relative flex-1",
    // Kept through its exit while the panel collapses around it.
    !primaryPresence.mounted && !memoryOpen && "hidden",
  );
  const notesClass = cn(
    "h-full min-w-0 bg-card",
    notesLeaving
      ? cn("absolute inset-y-0 right-0 z-[1] slide-out-to-right-8", LEAVE)
      : cn(
          "animate-in fade-in-0 slide-in-from-right-8 duration-200 ease-out motion-reduce:animate-none",
          layout === "tabs"
            ? cn("absolute inset-0", PANE_MOTION, behindTab("notes") && "pointer-events-none translate-x-3 opacity-0")
            : layout === "side"
              ? "relative shrink-0"
              : "relative flex-1",
        ),
  );
  const notesStyle = notesLeaving
    ? { width: sharedLayout === "side" && primaryOpen ? notesWidth : "100%" }
    : layout === "side"
      ? { width: notesWidth }
      : undefined;
  const primaryStyle = primaryLeaving
    ? { width: sharedLayout === "side" ? canvasWidth - notesWidth : "100%" }
    : undefined;
  const primaryLabel = slidesOpen
    ? t("notes:pane.slides", "Slides")
    : t("notes:pane.reader", "Reader");

  return (
    <div
      className={cn(
        "grid flex-1 min-h-0",
        // No width transition while dragging the divider, so it tracks the cursor.
        !dragging && "transition-[grid-template-columns] duration-200",
      )}
      style={{ gridTemplateColumns: `1fr ${rightPanelOpen ? canvasWidth : 0}px` }}
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
      {/* Right panel — shared slot for the Citation Canvas, Slides, Memory
          and Notes. Stays mounted (collapsed to 0 width when closed) so
          kept-alive paper iframes survive open/close cycles without
          re-rendering. */}
      <div className="relative min-h-0 overflow-hidden">
        {rightPanelOpen && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={t("panel.resize")}
            onPointerDown={onPointerDown}
            className="absolute left-0 top-0 z-10 h-full w-1.5 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
          />
        )}
        {/* Disable pointer events while dragging so the cursor can't enter
            cross-document iframes and swallow window pointermove events. */}
        <div className={cn("flex h-full flex-col", dragging && "pointer-events-none")}>
          {/* Reader | Notes tabs while the two share a narrow panel: always
              mounted, the bar's height opens and closes. */}
          <div
            aria-hidden={layout !== "tabs" || undefined}
            inert={layout !== "tabs" ? true : undefined}
            className={cn(
              "grid shrink-0 transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
              layout === "tabs" ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
            )}
          >
            <div className="overflow-hidden">
              <div
                role="tablist"
                aria-label={t("notes:pane.tabsLabel", "Right panel")}
                className="flex gap-1 border-b border-l border-border bg-card px-2 py-1"
                onKeyDown={(e) => {
                  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                  e.preventDefault();
                  const next = activeTab === "reader" ? "notes" : "reader";
                  setActiveTab(next);
                  e.currentTarget
                    .querySelector<HTMLButtonElement>(`[data-pane-tab="${next}"]`)
                    ?.focus();
                }}
              >
                {(["reader", "notes"] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    data-pane-tab={tab}
                    aria-selected={activeTab === tab}
                    tabIndex={activeTab === tab ? 0 : -1}
                    onClick={() => setActiveTab(tab)}
                    className={cn(
                      "rounded px-2 py-0.5 text-xs font-medium transition-colors duration-150 motion-reduce:transition-none",
                      activeTab === tab
                        ? "bg-foreground/10 text-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    {tab === "reader" ? primaryLabel : t("notes:pane.label", "Notes")}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="relative flex min-h-0 flex-1 overflow-hidden">
            <div
              className={primaryClass}
              style={primaryStyle}
              {...hideProps(primaryLeaving || behindTab("reader"))}
            >
              {/* Fix 2: CitationCanvas is ALWAYS mounted (never conditionally
                  removed) so its fetched-document cache (iframes/PDF state)
                  stays alive across open/close cycles. Hidden once closed —
                  after its exit when it leaves alone — so closing Memory with
                  the canvas closed does NOT reveal it. */}
              <div
                className="h-full w-full"
                hidden={!canvasShown}
                aria-hidden={!canvasOpen || undefined}
                {...(!canvasOpen ? { inert: true } : {})}
              >
                <PanelLoadBoundary onRetry={CitationCanvas.reset}>
                  <Suspense fallback={null}>
                    <CitationCanvas />
                  </Suspense>
                </PanelLoadBoundary>
              </div>

              {/* Memory Manager: absolutely overlays the Citation Canvas inside
                  the right-panel column when memoryOpen is true. With no backend
                  session yet (empty chat) it shows global (user) memories only;
                  project (session) memory needs at least one sent message. */}
              {memoryOpen && (
                <div className="absolute inset-0 flex flex-col bg-card border-l border-border overflow-hidden">
                  <div className="shrink-0 px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-border">
                    {t("panel.memory")}
                  </div>
                  <div className="flex-1 overflow-y-auto">
                    <PanelLoadBoundary onRetry={MemoryManager.reset}>
                      <Suspense fallback={null}>
                        <MemoryManager sessionId={backendSessionId} />
                      </Suspense>
                    </PanelLoadBoundary>
                  </div>
                </div>
              )}

              {/* Slides panel: absolutely overlays the right-panel column. Kept
                  MOUNTED once opened (hidden when closed) so its parsed +
                  rasterized PDF survives a swap to the Canvas and back — no
                  refetch, no re-render, no "loading" flash. Only mounts when a
                  backend session exists (a deck is session-scoped). */}
              {slidesMounted && backendSessionId !== null && (
                <div
                  className="absolute inset-0 flex flex-col bg-card border-l border-border overflow-hidden"
                  hidden={!slidesShown}
                  aria-hidden={!slidesOpen || undefined}
                  {...(!slidesOpen ? { inert: true } : {})}
                >
                  <div className="shrink-0 px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-border">
                    {t("panel.slides")}
                  </div>
                  <div className="flex-1 min-h-0 overflow-hidden">
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
                  </div>
                </div>
              )}
            </div>

            {/* Side by side: the divider trades width between the reader and
                Notes; the panel's left edge sizes only the reader. */}
            {layout === "side" && !notesLeaving && (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label={t("notes:pane.resize", "Resize Reader and Notes")}
                onPointerDown={divider.onPointerDown}
                className="relative z-10 w-1.5 shrink-0 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
              />
            )}

            {notesPresence.mounted && (
              <div
                className={cn(notesClass, layout !== "side" && "border-l border-border")}
                style={notesStyle}
                {...hideProps(notesLeaving || behindTab("notes"))}
              >
                <PanelLoadBoundary onRetry={NotesPane.reset}>
                  <Suspense fallback={null}>
                    <NotesPane onClose={closeNotes} />
                  </Suspense>
                </PanelLoadBoundary>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
