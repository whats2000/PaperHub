import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { useDeckSync } from "@/hooks/useDeckSync";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import type { ChatMessage, DeckMeta } from "@/types/domain";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => server.resetHandlers());

beforeEach(() => {
  localStorage.clear();
  useChatStore.setState({
    sessions: [],
    activeSessionId: null,
    _nextId: 1,
    referencesBySession: {},
    composerDraft: "",
  });
  useSlidesStore.setState({
    open: false,
    deckBySession: {},
    currentPageBySession: {},
  });
});

function deckMeta(sessionId: number, pageCount: number): DeckMeta {
  return {
    deck_id: sessionId * 100,
    session_id: sessionId,
    page_count: pageCount,
    theme: "metropolis",
    status: "ok",
    plan: { title: `Deck for ${sessionId}` },
    speaker_notes: { "1": "note one" },
    contributing_paper_ids: [1, 2],
    updated_at: "2026-05-24T00:00:00Z",
  };
}

function assistantMsg(runId: number): ChatMessage {
  return {
    role: "assistant",
    content: "here are your slides",
    run_id: runId,
    status: "ok",
  };
}

describe("useDeckSync", () => {
  it("hydrates the slides store AND the chat message deck from GET /sessions/:id/deck", async () => {
    server.use(
      http.get(`${API_BASE_URL}/sessions/7/deck`, () =>
        HttpResponse.json(deckMeta(7, 9)),
      ),
    );

    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "S1",
          messages: [assistantMsg(42)],
          backend_session_id: 7,
        },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
    });

    renderHook(() => useDeckSync());

    // Slides store populated (for the panel badge).
    await waitFor(() => {
      expect(useSlidesStore.getState().deckBySession[7]).toBeDefined();
    });
    expect(useSlidesStore.getState().deckBySession[7]?.page_count).toBe(9);

    // The chat message's deck is re-attached so the DeckChip re-appears
    // after a refresh (this is the BUG2 fix — message.deck is otherwise
    // wiped by hydrateSessionMessages).
    const msg = useChatStore.getState().sessions[0]?.messages[0];
    expect(msg?.deck).toBeDefined();
    expect(msg?.deck?.deck_id).toBe(700);
    expect(msg?.deck?.page_count).toBe(9);
  });

  it("clears the deck on the message and store when the backend returns 404", async () => {
    server.use(
      http.get(`${API_BASE_URL}/sessions/8/deck`, () =>
        HttpResponse.text("no deck", { status: 404 }),
      ),
    );

    // Pre-seed a stale deck on both the message and the store.
    const stale = deckMeta(8, 3);
    useChatStore.setState({
      sessions: [
        {
          id: 2,
          title: "S2",
          messages: [
            {
              ...assistantMsg(50),
              deck: {
                deck_id: stale.deck_id,
                session_id: 8,
                page_count: 3,
                title: "stale",
                status: "ok",
                contributing_papers: [],
                has_notes: false,
              },
            },
          ],
          backend_session_id: 8,
        },
      ],
      activeSessionId: 2,
      _nextId: 3,
      referencesBySession: {},
    });

    renderHook(() => useDeckSync());

    await waitFor(() => {
      expect(useChatStore.getState().sessions[0]?.messages[0]?.deck).toBeUndefined();
    });
    expect(useSlidesStore.getState().deckBySession[8]).toBeUndefined();
  });

  it("shows the right deck per session on switch (A has a deck, B has none)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/sessions/7/deck`, () =>
        HttpResponse.json(deckMeta(7, 5)),
      ),
      http.get(`${API_BASE_URL}/sessions/8/deck`, () =>
        HttpResponse.text("no deck", { status: 404 }),
      ),
    );

    useChatStore.setState({
      sessions: [
        { id: 1, title: "A", messages: [assistantMsg(1)], backend_session_id: 7 },
        { id: 2, title: "B", messages: [assistantMsg(2)], backend_session_id: 8 },
      ],
      activeSessionId: 1,
      _nextId: 3,
      referencesBySession: {},
    });

    renderHook(() => useDeckSync());

    await waitFor(() => {
      expect(useSlidesStore.getState().deckBySession[7]).toBeDefined();
    });

    // Switch to session B (no deck).
    useChatStore.setState({ activeSessionId: 2 });

    await waitFor(() => {
      // B never gets a deck slot.
      expect(useSlidesStore.getState().deckBySession[8]).toBeUndefined();
    });
    // A's deck slot is untouched (per-session, not stale-shared).
    expect(useSlidesStore.getState().deckBySession[7]).toBeDefined();
    // B's active message must NOT show A's deck.
    const sessB = useChatStore.getState().sessions.find((s) => s.id === 2);
    expect(sessB?.messages[0]?.deck).toBeUndefined();
  });

  it("does not clobber an in-flight streaming turn", async () => {
    const handler = vi.fn(() => HttpResponse.json(deckMeta(7, 4)));
    server.use(http.get(`${API_BASE_URL}/sessions/7/deck`, handler));

    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "S1",
          messages: [{ ...assistantMsg(99), status: "streaming" }],
          backend_session_id: 7,
        },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
    });

    renderHook(() => useDeckSync());

    // Wait a tick; the streaming guard must prevent any fetch.
    await new Promise((r) => setTimeout(r, 30));
    expect(handler).not.toHaveBeenCalled();
    // Streaming message left intact, no deck attached.
    expect(useChatStore.getState().sessions[0]?.messages[0]?.deck).toBeUndefined();
  });
});
