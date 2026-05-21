import { useEffect } from "react";

import { fetchSessionMessages, listSessions } from "@/lib/api";
import { useChatStore } from "@/store/chat";

/**
 * Makes the backend the source of truth for both the session LIST and each
 * session's MESSAGE record.
 *
 * Sessions used to live only in this browser's localStorage, so a chat started
 * on one device was invisible on another — and a stale local id could even
 * crash the backend's chat endpoint. This hook:
 *
 *  1. On mount, fetches GET /sessions and mirrors it into the store — adding
 *     backend-of-record sessions not present locally AND pruning local
 *     sessions whose backend row is gone (deleted elsewhere or never
 *     persisted). Every device shows the same set; no phantom/ghost chats.
 *  2. Whenever a session becomes active (mount, switch, reload), re-fetches
 *     its history from GET /sessions/{id}/messages and REPLACES the local
 *     copy. Without this the chat record was effectively local-only: once a
 *     browser had cached a session's messages it never re-read the DB, so a
 *     turn added on another device never showed up here. The replace is
 *     skipped for an in-flight (streaming) turn so live state isn't clobbered.
 *
 * localStorage stays as a cache; failures degrade silently (a warn, no toast)
 * so a backend hiccup never blocks the UI.
 */
export function useSessionsSync(): void {
  const reconcileBackendSessions = useChatStore(
    (s) => s.reconcileBackendSessions,
  );
  const hydrateSessionMessages = useChatStore((s) => s.hydrateSessionMessages);

  // 1. Pull the cross-device session list once on mount and mirror it.
  useEffect(() => {
    let cancelled = false;
    listSessions()
      .then((summaries) => {
        if (!cancelled) reconcileBackendSessions(summaries);
      })
      .catch((err: unknown) => {
        console.warn("[useSessionsSync] failed to list sessions:", err);
      });
    return () => {
      cancelled = true;
    };
  }, [reconcileBackendSessions]);

  // 2. Re-sync the active session's message record from the DB on every
  //    activation (mount / switch). Keyed on activeSessionId only, so it fires
  //    when the user opens a session — NOT on every local message change (that
  //    would refetch after each turn). hydrateSessionMessages re-checks the
  //    streaming guard at apply time, so a fetch that resolves mid-turn can't
  //    clobber an in-flight response.
  const activeSessionId = useChatStore((s) => s.activeSessionId);

  useEffect(() => {
    if (activeSessionId === null) return;
    const sess = useChatStore
      .getState()
      .sessions.find((x) => x.id === activeSessionId);
    if (!sess || sess.backend_session_id === null) return;
    if (sess.messages.some((m) => m.status === "streaming")) return;

    const backendId = sess.backend_session_id;
    let cancelled = false;
    fetchSessionMessages(backendId)
      .then((messages) => {
        if (!cancelled) hydrateSessionMessages(activeSessionId, messages);
      })
      .catch((err: unknown) => {
        console.warn(
          `[useSessionsSync] failed to load history for session ${backendId}:`,
          err,
        );
      });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId, hydrateSessionMessages]);
}
