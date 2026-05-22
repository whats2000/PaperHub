import { useEffect } from "react";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import { getDeck } from "@/lib/api";

/**
 * Top-level effect that fetches the active session's deck metadata from the
 * backend whenever the session's backend_session_id changes. Mirrors the
 * useReferencesSync pattern: silent 404 (no deck yet) is expected and
 * ignored; other errors are logged as warnings only.
 *
 * Called at the ChatPage level so the Slides panel badge + state are
 * pre-populated before the user opens the panel.
 */
export function useDeckSync(): void {
  const backendSessionId = useChatStore((s) => {
    if (s.activeSessionId === null) return null;
    return s.sessions.find((x) => x.id === s.activeSessionId)?.backend_session_id ?? null;
  });
  const setDeck = useSlidesStore((s) => s.setDeck);

  useEffect(() => {
    if (backendSessionId === null) return;
    let cancelled = false;
    getDeck(backendSessionId)
      .then((d) => {
        if (cancelled) return;
        setDeck(backendSessionId, {
          deck_id: d.deck_id,
          session_id: d.session_id,
          page_count: d.page_count,
          title: (d.plan as { title?: string })?.title ?? "Slides",
          status: d.status,
          contributing_papers: [],
          has_notes: Object.keys(d.speaker_notes).length > 0,
        });
      })
      .catch(() => undefined); // 404 = no deck yet; silence all errors
    return () => {
      cancelled = true;
    };
  }, [backendSessionId, setDeck]);
}
