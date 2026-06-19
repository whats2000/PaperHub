import { useCallback, useRef } from "react";
import type {
  RoutingDecision,
  SearchResultCandidate,
  ToolCallRecord,
  DeckEventData,
} from "@/types/domain";
import { streamChat } from "@/lib/sse";
import { cancelRun, listSessionReferences } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";

interface SessionData { run_id: number; session_id: number; }
interface ToolStepData { record: ToolCallRecord; }
interface RoutingData { run_id: number; branch: string; decision: RoutingDecision; }
interface TokenData { run_id: number; branch: string; text: string; }
interface FinalData { run_id: number; branch: string; message_id: number; content: string; }
interface ErrorData { run_id: number; branch: string; message: string; }
interface SearchResultsData {
  run_id: number;
  candidates: SearchResultCandidate[];
}

export function useChatStream() {
  const abortRef = useRef<AbortController | null>(null);
  const userStoppedRef = useRef(false);
  const runIdRef = useRef<number | null>(null);
  const sessionIdRef = useRef<number | null>(null);
  const store = useChatStore;

  const send = useCallback(async (sessionId: number, userMessage: string, opts?: { skipUserAppend?: boolean }) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    userStoppedRef.current = false;
    runIdRef.current = null;
    sessionIdRef.current = sessionId;

    // Snapshot prior turns BEFORE we append the new user + assistant placeholder.
    const currentSession = store.getState().sessions.find((s) => s.id === sessionId);
    const priorMessages = currentSession?.messages ?? [];
    const backendSessionId = currentSession?.backend_session_id ?? null;
    const history = priorMessages
      .filter((m) => m.status !== "error" && m.status !== "streaming")
      .filter((m) => m.content.length > 0)
      .map((m) => ({ role: m.role, content: m.content }));

    if (!opts?.skipUserAppend) {
      store.getState().appendMessage(sessionId, {
        role: "user", content: userMessage, run_id: null,
      });
    }
    store.getState().appendMessage(sessionId, {
      role: "assistant", content: "", run_id: null, status: "streaming",
    });
    let runId: number | null = null;
    // True once the error has been rendered inline (mid-stream case). The outer
    // catch checks this to decide whether to re-throw to ChatPage's toast.
    let handledInline = false;

    // When this session has a deck open, tell the backend which slide is on
    // screen so the Report Agent's deck-command classifier can resolve
    // "edit this slide" to the visible page, and whether the slide is
    // attached as context for the QA agent.
    // currentViewPage is gated on hasDeck only (deck edits need it even when
    // the panel is closed); slideAttached additionally requires slides.open so
    // slide context is never appended when the Slides panel is not visible.
    const slides = useSlidesStore.getState();
    const hasDeck =
      backendSessionId !== null && !!slides.deckBySession[backendSessionId];
    const currentViewPage =
      hasDeck ? (slides.currentPageBySession[backendSessionId] ?? 1) : undefined;
    const slideAttached =
      hasDeck && slides.open
        ? (slides.slideAttachedBySession[backendSessionId] ?? true)
        : false;

    try {
      await streamChat(
        {
          session_id: backendSessionId,
          user_message: userMessage,
          history,
          ...(currentViewPage !== undefined ? { current_view_page: currentViewPage } : {}),
          slide_attached: slideAttached,
        },
        {
          onEvent: (event, data) => {
            if (event === "session") {
              const s = data as SessionData;
              store.getState().patchSessionBackendId(sessionId, s.session_id);
              if (runId === null) {
                runId = s.run_id;
                runIdRef.current = runId;
                store.getState().patchAssistantRunId(sessionId, runId);
              }
            } else if (event === "tool_step") {
              const rec = (data as ToolStepData).record;
              if (runId === null) {
                runId = rec.run_id;
                runIdRef.current = runId;
                store.getState().patchAssistantRunId(sessionId, runId);
              }
              store.getState().appendTrace(sessionId, rec.run_id, rec);
            } else if (event === "routing_decision") {
              const d = data as RoutingData;
              if (runId === null) {
                runId = d.run_id;
                runIdRef.current = runId;
                store.getState().patchAssistantRunId(sessionId, runId);
              }
              store.getState().setRouting(sessionId, d.run_id, d.decision);
            } else if (event === "token") {
              const t = data as TokenData;
              store.getState().appendToken(sessionId, t.run_id, t.text);
            } else if (event === "search_results") {
              const s = data as SearchResultsData;
              if (runId === null) {
                runId = s.run_id;
                runIdRef.current = runId;
                store.getState().patchAssistantRunId(sessionId, runId);
              }
              store
                .getState()
                .setSearchResults(sessionId, s.run_id, s.candidates);
              // If the agent auto-added any candidates, refresh the
              // Reference Sources panel — the chat-stream wrote rows
              // server-side, but the panel only pulls on
              // backend_session_id changes, so it would otherwise
              // stay empty until reload. Use the latest backend
              // session id from the store, since the session event
              // may have arrived earlier this stream and updated it.
              if (s.candidates.some((c) => c.auto_added)) {
                const latest = store
                  .getState()
                  .sessions.find((sess) => sess.id === sessionId)
                  ?.backend_session_id;
                if (latest != null) {
                  void listSessionReferences(latest)
                    .then((refs) =>
                      store.getState().setReferences(latest, refs),
                    )
                    .catch(() => undefined);
                }
              }
            } else if (event === "deck") {
              const d = data as DeckEventData;
              store.getState().setDeckOnMessage(sessionId, d);
              const slidesStore = useSlidesStore.getState();
              slidesStore.setDeck(d.session_id, d);
              // Only reset to page 1 for a NEW deck — an edit/notes follow-up
              // must not jump the panel back to the first page.
              if (slidesStore.currentPageBySession[d.session_id] === undefined) {
                slidesStore.setCurrentPage(d.session_id, 1);
              }
            } else if (event === "final") {
              const f = data as FinalData;
              store.getState().finaliseMessage(sessionId, f.run_id, f.content);
            } else if (event === "error") {
              const e = data as ErrorData;
              store.getState().errorMessage(sessionId, e.run_id, e.message);
            }
          },
          onError: (err) => {
            const msg = err instanceof Error ? err.message : String(err);
            if (runId !== null) {
              // Mid-stream: bubble has context, inline error is enough.
              store.getState().errorMessage(sessionId, runId, msg);
              handledInline = true;
            } else {
              // Pre-event: placeholder bubble is empty, need both surfaces.
              store.getState().failPendingAssistant(sessionId, msg);
              // Don't set handledInline — outer catch re-throws → ChatPage toasts.
            }
          },
        },
        abortRef.current.signal,
      );
    } catch (err) {
      // User clicked Stop: the turn was already retracted synchronously on the
      // click. Swallow regardless of whether the lib rejected or resolved.
      if (userStoppedRef.current) return;
      // fetchEventSource may throw synchronously before onerror fires
      // (e.g. CORS preflight reject, immediate connection refused). In that
      // case onError didn't run; runId is still null; treat as pre-event.
      if (!handledInline && runId === null) {
        const msg = err instanceof Error ? err.message : String(err);
        store.getState().failPendingAssistant(sessionId, msg);
      }
      // Only re-throw for pre-event failures so ChatPage's toast fires.
      // Mid-stream failures stay inline-only.
      if (!handledInline) {
        throw err;
      }
    }
  }, [store]);

  const stop = useCallback(() => {
    // SYNCHRONOUS + IMMEDIATE — react in this same tick; do NOT await the abort.
    userStoppedRef.current = true;
    // Resolve sid: prefer the ref set by send(); fall back to activeSessionId
    // so Stop works on a reattached/refreshed turn where send() never ran.
    const sid = sessionIdRef.current ?? store.getState().activeSessionId;
    let rid = runIdRef.current;
    if (rid === null && sid !== null) {
      // No live ref: use the trailing streaming/processing assistant's run_id.
      // "processing" covers the reattach case (hydrateSessionMessages injects
      // a processing placeholder whose run_id points at the in-flight backend run).
      const sess = store.getState().sessions.find((s) => s.id === sid);
      const last = sess?.messages[sess.messages.length - 1];
      if (
        last?.role === "assistant" &&
        (last.status === "streaming" || last.status === "processing") &&
        last.run_id != null
      ) {
        rid = last.run_id;
      }
    }
    abortRef.current?.abort();                        // 1) cut the stream
    if (sid !== null) {                               // 2) retract the pair + restore text
      const restored = store.getState().retractTurn(sid);
      if (restored) store.getState().requestComposerText(restored);
    }
    if (rid !== null) void cancelRun(rid).catch(() => undefined);  // 3) kill the backend run
  }, [store]);

  return { send, stop };
}
