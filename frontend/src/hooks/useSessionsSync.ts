import { useEffect, useRef } from "react";

import { fetchSessionMessages, listSessions } from "@/lib/api";
import { useChatStore } from "@/store/chat";

/**
 * Makes the backend the source of truth for the session list.
 *
 * Sessions used to live only in this browser's localStorage, so a chat started
 * on one device was invisible on another — and a stale local id could even
 * crash the backend's chat endpoint. This hook:
 *
 *  1. On mount, fetches GET /sessions and mirrors it into the store — adding
 *     backend-of-record sessions not present locally AND pruning local
 *     sessions whose backend row is gone (deleted elsewhere or never
 *     persisted). This keeps every device showing the same set and stops
 *     phantom/ghost chats from lingering in one browser's localStorage.
 *  2. Lazily replays a session's history (GET /sessions/{id}/messages) the
 *     first time it becomes active with a backend id but no loaded messages —
 *     e.g. a session that arrived from the backend list on another device.
 *
 * localStorage stays as a cache; failures degrade silently (a warn, no toast)
 * so a backend hiccup never blocks the UI.
 */
export function useSessionsSync(): void {
  const reconcileBackendSessions = useChatStore(
    (s) => s.reconcileBackendSessions,
  );
  const hydrateSessionMessages = useChatStore((s) => s.hydrateSessionMessages);

  // Sessions whose history we've already fetched, so an empty backend-only
  // session (or a re-select) doesn't refetch on every active-id change.
  const hydratedRef = useRef<Set<number>>(new Set());

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

  // 2. Lazily hydrate the active session's history when it has a backend id
  //    but no loaded messages yet. Selectors return primitives so their
  //    snapshots stay referentially stable (an object literal would loop).
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const backendId = useChatStore((s) => {
    if (s.activeSessionId === null) return null;
    const sess = s.sessions.find((x) => x.id === s.activeSessionId);
    if (!sess || sess.backend_session_id === null) return null;
    return sess.messages.length > 0 ? null : sess.backend_session_id;
  });

  useEffect(() => {
    if (activeSessionId === null || backendId === null) return;
    if (hydratedRef.current.has(backendId)) return;
    hydratedRef.current.add(backendId);

    const localId = activeSessionId;
    let cancelled = false;
    fetchSessionMessages(backendId)
      .then((messages) => {
        if (!cancelled) hydrateSessionMessages(localId, messages);
      })
      .catch((err: unknown) => {
        // Allow a later retry if the fetch failed.
        hydratedRef.current.delete(backendId);
        console.warn(
          `[useSessionsSync] failed to load history for session ${backendId}:`,
          err,
        );
      });
    return () => {
      cancelled = true;
    };
  }, [backendId, activeSessionId, hydrateSessionMessages]);
}
