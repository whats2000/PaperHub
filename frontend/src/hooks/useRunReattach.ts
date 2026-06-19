import { useEffect, useRef } from "react";

import { fetchRunEvents, fetchSessionMessages } from "@/lib/api";
import { useChatStore } from "@/store/chat";

const TERMINAL_STATUSES = new Set(["ok", "error", "cancelled", "interrupted"]);
const POLL_INTERVAL_MS = 1000;

/**
 * A10 (FR-15): Reattach poller for a returning client (refresh / second device).
 *
 * When the active session has a trailing `processing` assistant placeholder with
 * a non-null `run_id` (injected by `hydrateSessionMessages`), this hook polls
 * `GET /chat/runs/{run_id}/events?since={cursor}` every ~1 s and feeds each
 * returned event through `applyRunEvent` — the same store mutations the live
 * SSE path uses (D7, high-fidelity replay).
 *
 * On a terminal response (`ok | error | cancelled | interrupted`), performs a
 * single authoritative settle via `GET /sessions/{id}/messages` →
 * `hydrateSessionMessages`, which reconciles all four outcomes:
 *   ok         → answer text in place
 *   interrupted → interrupted bubble
 *   error       → error bubble
 *   cancelled   → both rows gone
 *
 * Stops polling on: terminal, active-session switch, component unmount, or
 * `document.hidden` (tab hidden).
 *
 * Guards: no overlapping in-flight fetches; swallows transient fetch errors
 * (keeps cursor, retries next tick); no-ops when there is nothing to poll.
 */
export function useRunReattach(): void {
  const activeSessionId = useChatStore((s) => s.activeSessionId);

  // Derive the trailing processing run_id from the active session's messages.
  const processingRunId = useChatStore((s) => {
    if (s.activeSessionId === null) return null;
    const sess = s.sessions.find((x) => x.id === s.activeSessionId);
    if (!sess) return null;
    const msgs = sess.messages;
    const last = msgs[msgs.length - 1];
    if (last === undefined) return null;
    if (last.role === "assistant" && last.status === "processing" && last.run_id !== null) {
      return last.run_id;
    }
    return null;
  });

  // Refs for mutable polling state (not part of React state — no re-renders).
  const cursorRef = useRef<number>(0);
  const inFlightRef = useRef<boolean>(false);
  const settledRef = useRef<boolean>(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (activeSessionId === null || processingRunId === null) return;

    // Stop immediately if document is hidden — optional resume on visibility
    // change is handled via the visibilitychange listener below.
    if (document.hidden) return;

    // Reset per-run state when the run changes.
    cursorRef.current = 0;
    inFlightRef.current = false;
    settledRef.current = false;

    const sessionId = activeSessionId;
    const runId = processingRunId;

    const poll = async (): Promise<void> => {
      // Guard: don't start a second fetch while one is in flight.
      if (inFlightRef.current || settledRef.current) return;
      inFlightRef.current = true;

      try {
        const resp = await fetchRunEvents(runId, cursorRef.current);

        // Advance cursor.
        cursorRef.current = resp.next_cursor;

        // Apply each event through the shared reducer (D7).
        const store = useChatStore.getState();
        for (const evt of resp.events) {
          store.applyRunEvent(sessionId, evt);
        }

        // On terminal status: settle via an authoritative /messages refetch.
        if (TERMINAL_STATUSES.has(resp.status)) {
          settledRef.current = true;
          stopPolling();

          // Get the backend session id for the settle refetch.
          const sess = useChatStore.getState().sessions.find((x) => x.id === sessionId);
          const backendId = sess?.backend_session_id;
          if (backendId !== null && backendId !== undefined) {
            try {
              const messages = await fetchSessionMessages(backendId);
              useChatStore.getState().hydrateSessionMessages(sessionId, messages);
            } catch {
              // Settle fetch failed — swallow; the in-progress state events
              // already partially applied, which is still better than nothing.
            }
          }
        }
      } catch {
        // Transient error (network blip, 5xx): swallow, keep cursor, retry on
        // next tick. Log at warn level for observability without breaking the UI.
        console.warn(`[useRunReattach] poll error for run ${runId}, will retry`);
      } finally {
        inFlightRef.current = false;
      }
    };

    const stopPolling = (): void => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };

    // Kick off the first poll immediately, then on interval.
    void poll();
    intervalRef.current = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    // Stop polling when the tab is hidden; resume when it becomes visible again
    // (unless already settled by a terminal response).
    const handleVisibilityChange = (): void => {
      if (document.hidden) {
        stopPolling();
      } else if (!settledRef.current) {
        // Tab is visible again and the run hasn't finished — re-arm the poller.
        void poll();
        if (intervalRef.current === null) {
          intervalRef.current = setInterval(() => {
            void poll();
          }, POLL_INTERVAL_MS);
        }
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      stopPolling();
      settledRef.current = true; // prevent any in-flight from re-scheduling
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [activeSessionId, processingRunId]);
}
