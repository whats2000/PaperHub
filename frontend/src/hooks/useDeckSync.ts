import { useEffect } from "react";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import { getDeck } from "@/lib/api";
import type { DeckEventData } from "@/types/domain";

/**
 * Top-level effect that hydrates the active session's deck from the backend
 * whenever the session's `backend_session_id` changes (mount, switch, reload).
 *
 * The `deck` SSE event populates BOTH the slides store (`deckBySession`, drives
 * the Slides panel) AND the chat message (`message.deck`, drives the in-chat
 * DeckChip). But on refresh / re-activation those are transient: the slides
 * store does not persist deck data, and `hydrateSessionMessages` (re-syncing
 * the chat record from the DB) drops `message.deck`. So the DeckChip vanished
 * after a reload even though the backend persisted the deck (`decks` table,
 * served at GET /sessions/{id}/deck).
 *
 * This hook re-fetches that endpoint and restores BOTH surfaces — mirroring how
 * paper-search cards persist + replay. It is the deck half of the "hydrate this
 * session from the DB on activation" step:
 *  - deck present  → set the slides store + re-attach `message.deck` so the chip
 *    re-appears and the panel can open to it.
 *  - 404 / error   → clear both for this session, so switching to a deckless
 *    session never shows a stale deck.
 *
 * The streaming guard mirrors useSessionsSync's message re-sync: a live slide
 * generation in flight already drives both surfaces via the SSE event, so we
 * skip the fetch to avoid clobbering it (the DB row may lag the live state).
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
  const setDeckOnMessage = useChatStore((s) => s.setDeckOnMessage);

  useEffect(() => {
    if (backendSessionId === null) return;

    // The slides store is keyed by BACKEND session id (`deckBySession`,
    // matching the SSE event's `session_id`); the chat message lives under the
    // LOCAL session id. Resolve the local id for the message patch.
    const activeSessionId = useChatStore.getState().activeSessionId;
    if (activeSessionId === null) return;
    const sess = useChatStore
      .getState()
      .sessions.find((x) => x.id === activeSessionId);

    // Skip while a turn is streaming — the SSE `deck` event is the source of
    // truth mid-generation; the persisted row may not exist yet.
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
        // Re-attach to the chat message so the DeckChip survives a refresh.
        setDeckOnMessage(activeSessionId, event);
      })
      .catch(() => {
        // 404 (no deck for this session) or transient error → clear both so a
        // deckless session never inherits the previously-active deck.
        if (cancelled) return;
        clearDeck(backendSessionId);
        setDeckOnMessage(activeSessionId, null);
      });
    return () => {
      cancelled = true;
    };
  }, [backendSessionId, setDeck, clearDeck, setDeckOnMessage]);
}
