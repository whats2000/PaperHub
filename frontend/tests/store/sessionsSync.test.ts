import { beforeEach, describe, expect, it } from "vitest";

import { useChatStore } from "@/store/chat";
import type { BackendMessage, SessionSummary } from "@/types/domain";

beforeEach(() => {
  localStorage.clear();
  useChatStore.setState({
    sessions: [],
    activeSessionId: null,
    _nextId: 1,
    referencesBySession: {},
    composerDraft: "",
  });
});

function summary(id: number, title: string): SessionSummary {
  return {
    id,
    title,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:01:00Z",
    message_count: 2,
  };
}

describe("reconcileBackendSessions", () => {
  it("adds backend sessions not present locally, keyed by backend id", () => {
    useChatStore.getState().reconcileBackendSessions([
      summary(7, "Flow matching"),
      summary(8, "RAG vs fine-tuning"),
    ]);
    const sessions = useChatStore.getState().sessions;
    expect(sessions).toHaveLength(2);
    const backendIds = sessions.map((s) => s.backend_session_id);
    expect(backendIds).toContain(7);
    expect(backendIds).toContain(8);
    expect(sessions.every((s) => s.messages.length === 0)).toBe(true);
    expect(useChatStore.getState()._nextId).toBe(3);
  });

  it("does not duplicate a session already known by backend id", () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "Local copy", messages: [], backend_session_id: 7 },
      ],
      _nextId: 2,
    });
    useChatStore.getState().reconcileBackendSessions([
      summary(7, "Server title"),
      summary(9, "Brand new"),
    ]);
    const sessions = useChatStore.getState().sessions;
    expect(sessions.filter((s) => s.backend_session_id === 7)).toHaveLength(1);
    expect(sessions.some((s) => s.backend_session_id === 9)).toBe(true);
  });

  it("adopts the backend title for matched sessions (source of truth)", () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "Stale local", messages: [], backend_session_id: 7 },
      ],
      _nextId: 2,
    });
    useChatStore.getState().reconcileBackendSessions([summary(7, "Server title")]);
    expect(useChatStore.getState().sessions[0]!.title).toBe("Server title");
  });

  it("prunes empty local sessions whose backend row is gone (deleted elsewhere)", () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "Gone on server", messages: [], backend_session_id: 7 },
        { id: 2, title: "Still here", messages: [], backend_session_id: 8 },
      ],
      activeSessionId: 2,
      _nextId: 3,
    });
    // Server only knows 8 now.
    useChatStore.getState().reconcileBackendSessions([summary(8, "Still here")]);
    const ids = useChatStore.getState().sessions.map((s) => s.backend_session_id);
    expect(ids).toEqual([8]);
  });

  it("STRICT: prunes a backend-id session gone from the DB even with local messages", () => {
    // A chat deleted on another device: its backend row is gone, but this
    // browser cached its messages. Strict mirror must drop it — otherwise it's
    // the "deleted in A, still in B" ghost.
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "Deleted elsewhere",
          messages: [
            { role: "user", content: "old", run_id: null },
            { role: "assistant", content: "reply", run_id: null },
          ],
          backend_session_id: 7,
        },
      ],
      activeSessionId: 1,
      _nextId: 2,
    });
    useChatStore.getState().reconcileBackendSessions([]);
    expect(useChatStore.getState().sessions).toHaveLength(0);
    // Active pointer cleared since the session it referenced is gone.
    expect(useChatStore.getState().activeSessionId).toBeNull();
  });

  it("keeps a local unsent draft (no backend id) — never a cross-device chat", () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "New chat", messages: [], backend_session_id: null },
      ],
      activeSessionId: 1,
      _nextId: 2,
    });
    useChatStore.getState().reconcileBackendSessions([]);
    expect(useChatStore.getState().sessions).toHaveLength(1);
    expect(useChatStore.getState().activeSessionId).toBe(1);
  });

  it("keeps null-backend drafts but prunes an active backend-id session gone from DB", () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "Draft", messages: [], backend_session_id: null },
        { id: 2, title: "Active, deleted on server", messages: [], backend_session_id: 99 },
      ],
      activeSessionId: 2,
      _nextId: 3,
    });
    // Server knows neither: the null draft stays (never cross-device); the
    // backend-id session 99 is gone from the DB, so strict mirror prunes it
    // even though it's active, and clears the active pointer.
    useChatStore.getState().reconcileBackendSessions([]);
    const ids = useChatStore.getState().sessions.map((s) => s.id);
    expect(ids).toEqual([1]);
    expect(useChatStore.getState().activeSessionId).toBeNull();
  });

  it("preserves the active session's local messages", () => {
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "Live",
          messages: [{ role: "user", content: "hi", run_id: null }],
          backend_session_id: 7,
        },
      ],
      activeSessionId: 1,
      _nextId: 2,
    });
    useChatStore.getState().reconcileBackendSessions([summary(7, "Server title")]);
    const sess = useChatStore.getState().sessions.find((s) => s.backend_session_id === 7);
    expect(sess?.messages).toHaveLength(1);
  });
});

describe("hydrateSessionMessages", () => {
  const history: BackendMessage[] = [
    { role: "user", content: "What is RAG?", run_id: 1, created_at: "t1" },
    {
      role: "assistant",
      content: "Retrieval augmented generation.",
      run_id: 1,
      created_at: "t2",
      routing_decision: {
        intent: "chitchat",
        model_tier: "small",
        confidence: 0.9,
        reasoning: "qa",
      },
    },
  ];

  it("fills an empty session and maps routing decision + ok status", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, history);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[0]!.content).toBe("What is RAG?");
    expect(msgs[1]!.status).toBe("ok");
    expect(msgs[1]!.routing_decision?.intent).toBe("chitchat");
  });

  it("replays search-result cards so they show on every device", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "find flow matching", run_id: 1, created_at: "t1" },
      {
        role: "assistant",
        content: "Here are some papers",
        run_id: 1,
        created_at: "t2",
        search_results: [
          {
            paper_id: "arxiv:1",
            title: "Flow matching",
            authors: ["A"],
            year: 2024,
            abstract: "x",
            arxiv_id: "1",
            has_open_pdf: true,
            reason: "relevant",
            finalize: true,
            auto_added: true,
            papers_id: 3,
            error: null,
            already_in_session: false,
          },
        ],
      },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs[1]!.search_results).toHaveLength(1);
    expect(msgs[1]!.search_results![0]!.title).toBe("Flow matching");
  });

  it("carries a replayed deck onto the assistant message (chip survives refresh)", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "make slides", run_id: 5, created_at: "t1" },
      {
        role: "assistant",
        content: "here is your deck",
        run_id: 5,
        created_at: "t2",
        deck: {
          deck_id: 700,
          session_id: 7,
          page_count: 9,
          title: "My Deck",
          status: "ok",
          contributing_papers: [{ id: 1 }, { id: 2 }],
          has_notes: true,
        },
      },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs[1]!.deck).toBeDefined();
    expect(msgs[1]!.deck?.deck_id).toBe(700);
    expect(msgs[1]!.deck?.page_count).toBe(9);
  });

  it("leaves deck undefined on a message replayed without a deck", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, history);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs[1]!.deck).toBeUndefined();
  });

  it("keeps the deck on refresh re-hydration (proves the card persists)", () => {
    // Simulate a refresh: the local store is wiped, then the DB replay re-fills
    // it. The deck must come back with the assistant message.
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    const withDeck: BackendMessage[] = [
      { role: "user", content: "make slides", run_id: 9, created_at: "t1" },
      {
        role: "assistant",
        content: "deck ready",
        run_id: 9,
        created_at: "t2",
        deck: {
          deck_id: 900,
          session_id: 7,
          page_count: 4,
          title: "Refreshed Deck",
          status: "ok",
          contributing_papers: [{ id: 3 }],
          has_notes: false,
        },
      },
    ];
    useChatStore.getState().hydrateSessionMessages(1, withDeck);
    const msg = useChatStore.getState().sessions[0]!.messages[1];
    expect(msg?.deck?.deck_id).toBe(900);
    expect(msg?.deck?.title).toBe("Refreshed Deck");
  });

  it("REPLACES a non-streaming session's messages with the DB copy", () => {
    // The backend is the source of truth for the chat record, so re-opening a
    // session refreshes it — picking up turns added on another device.
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "S",
          messages: [{ role: "user", content: "stale local", run_id: null, status: "ok" }],
          backend_session_id: 7,
        },
      ],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, history);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[0]!.content).toBe("What is RAG?");
  });

  it("does NOT clobber a session with an in-flight (streaming) turn", () => {
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "S",
          messages: [
            { role: "user", content: "live question", run_id: null, status: "ok" },
            { role: "assistant", content: "", run_id: null, status: "streaming" },
          ],
          backend_session_id: 7,
        },
      ],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, history);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[1]!.status).toBe("streaming");
  });

  it("drops system messages", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "system", content: "boot", run_id: null, created_at: "t0" },
      { role: "user", content: "hi", run_id: null, created_at: "t1" },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]!.role).toBe("user");
  });

  // ── A10: processing placeholder + interrupted status mapping ────────────────

  it("appends a processing placeholder when trailing user has run_status=running and no assistant follows", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "compute something", run_id: 88, created_at: "t1", run_status: "running" },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[1]!.role).toBe("assistant");
    expect(msgs[1]!.status).toBe("processing");
    expect(msgs[1]!.run_id).toBe(88);
    expect(msgs[1]!.content).toBe("");
  });

  it("does NOT append a placeholder when the trailing user has run_status=running but an assistant row already follows", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "hello", run_id: 99, created_at: "t1", run_status: "running" },
      { role: "assistant", content: "hi there", run_id: 99, created_at: "t2" },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[1]!.status).toBe("ok");
  });

  it("does NOT append a placeholder when the trailing user has no run_status=running", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "hello", run_id: null, created_at: "t1" },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]!.role).toBe("user");
  });

  it("maps run_status=interrupted to status: interrupted on an assistant message", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "hi", run_id: 10, created_at: "t1" },
      { role: "assistant", content: "partial", run_id: 10, created_at: "t2", run_status: "interrupted" },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs[1]!.status).toBe("interrupted");
  });

  it("maps run_status=error to status: error on an assistant message", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "hi", run_id: 11, created_at: "t1" },
      { role: "assistant", content: "", run_id: 11, created_at: "t2", run_status: "error" },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs[1]!.status).toBe("error");
  });

  it("keeps status: ok for normal completed assistant messages (no run_status)", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      _nextId: 2,
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "hi", run_id: 5, created_at: "t1" },
      { role: "assistant", content: "answer", run_id: 5, created_at: "t2" },
    ]);
    const msgs = useChatStore.getState().sessions[0]!.messages;
    expect(msgs[1]!.status).toBe("ok");
  });
});
