import { useEffect } from "react";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import { getDeck } from "@/lib/api";
import type { DeckEventData } from "@/types/domain";

/**
 * Top-level effect that hydrates the active session's deck from the backend
 * whenever the session's `backend_session_id` changes (mount, switch, reload).
 *
 * The `deck` SSE event populates the slides store (`deckBySession`, drives the
 * Slides panel) AND the chat message (`message.deck`, drives the in-chat
 * DeckChip). The DeckChip is now restored from the **message replay**
 * (`hydrateSessionMessages` carries `message.deck` straight from
 * GET /sessions/{id}/messages) — the robust, race-free source of truth — so
 * this hook NO LONGER patches the chat message.
 *
 * Its sole remaining job is hydrating the slides store so the **Slides panel**
 * can open to the active session's deck on mount / switch / reload (the slides
 * store does not persist deck data):
 *  - deck present  → set the slides store for this session.
 *  - 404 / error   → clear the slides store for this session, so switching to a
 *    deckless session never opens to a stale deck.
 *
 * The streaming guard mirrors useSessionsSync's message re-sync: a live slide
 * generation in flight already drives the store via the SSE event, so we skip
 * the fetch to avoid clobbering it (the DB row may lag the live state).
 */
export function useDeckSync(): void {
  const backendSessionId = useChatStore((s) => {
    if (s.activeSessionId === null) return null;
    return (
      s.sessions.find((x) => x.id === s.activeSessionId)?.backend_session_id ??
      null
    );
  });
  const setDeck = useSlidesStore((s) => s.setDeck);
  const clearDeck = useSlidesStore((s) => s.clearDeck);

  useEffect(() => {
    if (backendSessionId === null) return;

    // Skip while a turn is streaming — the SSE `deck` event is the source of
    // truth mid-generation; the persisted row may not exist yet.
    const activeSessionId = useChatStore.getState().activeSessionId;
    if (activeSessionId === null) return;
    const sess = useChatStore
      .getState()
      .sessions.find((x) => x.id === activeSessionId);
    if (sess?.messages.some((m) => m.status === "streaming")) return;

    let cancelled = false;
    getDeck(backendSessionId)
      .then((d) => {
        if (cancelled) return;
        const event: DeckEventData = {
          deck_id: d.deck_id,
          session_id: d.session_id,
          page_count: d.page_count,
          title: (d.plan as { title?: string } | null)?.title ?? "Slides",
          status: d.status,
          contributing_papers: [],
          has_notes: Object.keys(d.speaker_notes).length > 0,
        };
        setDeck(backendSessionId, event);
      })
      .catch(() => {
        // 404 (no deck for this session) or transient error → clear the slides
        // store so a deckless session never inherits the previous deck.
        if (cancelled) return;
        clearDeck(backendSessionId);
      });
    return () => {
      cancelled = true;
    };
  }, [backendSessionId, setDeck, clearDeck]);
}
