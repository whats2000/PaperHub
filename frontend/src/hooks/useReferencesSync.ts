import { useEffect } from "react";

import { listSessionReferences } from "@/lib/api";
import { useChatStore } from "@/store/chat";

/**
 * Top-level effect that refetches the active session's reference list from
 * the backend whenever the active session's `backend_session_id` changes
 * (including on mount with a rehydrated active session).
 *
 * Why this lives at the page level rather than inside the panel:
 * `referencesBySession` is persisted across reloads via zustand `persist`.
 * Without a top-level sync, the Sidebar's "References" tab badge — and the
 * panel's `enabled` toggles + paper kinds — read from the persisted cache
 * and never refresh until the user actively opens the panel (the only
 * place that previously called `listSessionReferences`). That caused
 * stale counts and stale `enabled` flags after browser reloads and after
 * switching between sessions on the Chats tab.
 *
 * The existing fetch inside `ReferenceSourcesPanel` is kept as
 * defence-in-depth: if this hook's fetch fails silently, opening the
 * panel still gives the user a fresh authoritative state.
 */
export function useReferencesSync(): void {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const backendSessionId = useChatStore((s) => {
    if (s.activeSessionId === null) return null;
    const sess = s.sessions.find((x) => x.id === s.activeSessionId);
    return sess?.backend_session_id ?? null;
  });
  const setReferences = useChatStore((s) => s.setReferences);

  useEffect(() => {
    if (backendSessionId === null) return;
    let cancelled = false;
    listSessionReferences(backendSessionId)
      .then((items) => {
        if (cancelled) return;
        setReferences(backendSessionId, items);
      })
      .catch((err: unknown) => {
        // Silent fail: stale cache is better than a toast on every reload.
        // The panel's own effect runs when the user opens it and offers a
        // second chance to recover.
        console.warn(
          `[useReferencesSync] failed to refresh references for session ${backendSessionId}:`,
          err,
        );
      });
    return () => {
      cancelled = true;
    };
    // `activeSessionId` is included so that switching to another session
    // that happens to share a backend id (impossible today, but cheap to
    // guard against) would still re-trigger; primarily the trigger is
    // `backendSessionId` becoming non-null or changing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendSessionId, activeSessionId]);
}
