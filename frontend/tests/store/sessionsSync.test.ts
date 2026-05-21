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
});
