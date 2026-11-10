import { beforeEach, describe, expect, it } from "vitest";

import { useChatStore } from "@/store/chat";

describe("addForkedSession", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it("adds a backend-of-record session, selects it, returns its local id", () => {
    const localId = useChatStore.getState().addForkedSession(99, "Fork of X");
    const state = useChatStore.getState();
    const sess = state.sessions.find((s) => s.id === localId);
    expect(sess).toBeDefined();
    expect(sess!.backend_session_id).toBe(99);
    expect(sess!.title).toBe("Fork of X");
    expect(sess!.messages).toEqual([]);
    expect(state.activeSessionId).toBe(localId);
  });

  it("does not duplicate when the backend id is already present", () => {
    const first = useChatStore.getState().addForkedSession(99, "Fork of X");
    const second = useChatStore.getState().addForkedSession(99, "Fork of X");
    expect(second).toBe(first);
    const count = useChatStore
      .getState()
      .sessions.filter((s) => s.backend_session_id === 99).length;
    expect(count).toBe(1);
  });
});

describe("idea_id (Plan H3)", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it("addForkedSession records the idea a chat was opened from", () => {
    const id = useChatStore.getState().addForkedSession(901, "Idea chat", null, 5);
    expect(useChatStore.getState().sessions.find((s) => s.id === id)?.idea_id).toBe(5);
  });

  it("reconcileBackendSessions mirrors idea_id from GET /sessions", () => {
    const local = useChatStore.getState().addForkedSession(901, "Idea chat");
    const row = (id: number, idea_id: number | null) => ({
      id,
      title: `Chat ${id}`,
      created_at: "2026-11-06T00:00:00Z",
      updated_at: "2026-11-06T00:00:00Z",
      message_count: 1,
      idea_id,
    });
    useChatStore.getState().reconcileBackendSessions([row(901, 5), row(902, null)]);
    const sessions = useChatStore.getState().sessions;
    expect(sessions.find((s) => s.id === local)?.idea_id).toBe(5);
    expect(sessions.find((s) => s.backend_session_id === 902)?.idea_id).toBeNull();
  });
});

describe("agent turns (H4)", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "t",
          backend_session_id: 5,
          messages: [
            { role: "user", content: "q", run_id: null },
            { role: "assistant", content: "Let me look.", run_id: 7, status: "streaming" },
          ],
        },
      ],
      activeSessionId: 1,
    });
  });
  const last = () => useChatStore.getState().sessions[0]!.messages[1]!;

  it("applies summary, answer_reset and UI events to the turn's message", () => {
    const s = useChatStore.getState();
    s.applyAgentEvent(1, 7, "answer_reset", {});
    expect(last().content).toBe("");
    s.applyAgentEvent(1, null, "paper_added", { papers_id: 3, paper_content_id: 2, title: "T" });
    s.applyAgentEvent(1, 7, "confirm_request", {
      action_id: "a", tool: "remove_paper", args_summary: "x", reason: "r",
    });
    s.applyAgentEvent(1, 7, "agent_summary", {
      run_id: 7, papers_read: 1, steps: 2, tools: ["add_paper"],
    });
    expect(last().agent_summary).toEqual({ papers_read: 1, steps: 2, tools: ["add_paper"] });
    expect(last().agent_events!.map((e) => e.event)).toEqual(["paper_added", "confirm_request"]);
    expect(last().agent_events![0]!.data).toMatchObject({ undoable: true });
    expect(last().agent_events![1]!.data).toMatchObject({ status: "pending" });

    s.patchAgentEvent(1, 7, 1, { status: "done", result: { content: "ok" } });
    expect(last().agent_events![1]!.data).toMatchObject({ status: "done" });
  });

  it("keeps one search batch per search_results event", () => {
    const s = useChatStore.getState();
    const c = (id: string) => ({
      paper_id: id, title: id, authors: [], year: null, abstract: null,
      arxiv_id: null, has_open_pdf: false, reason: "", finalize: false,
      auto_added: false, papers_id: null, error: null, already_in_session: false,
    });
    s.setSearchResults(1, 7, [c("a")]);
    s.setSearchResults(1, 7, [c("b"), c("c")]);
    expect(last().search_result_batches!.map((b) => b.length)).toEqual([1, 2]);
    expect(last().search_results!.map((x) => x.paper_id)).toEqual(["a", "b", "c"]);
  });

  it("hydrates the replayed summary, events and batches", () => {
    useChatStore.setState({
      sessions: [{ id: 1, title: "t", backend_session_id: 5, messages: [] }],
    });
    useChatStore.getState().hydrateSessionMessages(1, [
      { role: "user", content: "q", run_id: 7, created_at: "" },
      {
        role: "assistant", content: "a", run_id: 7, created_at: "", run_status: "ok",
        agent_summary: { papers_read: 0, steps: 1, tools: [] },
        agent_events: [{
          event: "confirm_request",
          data: { action_id: "a", tool: "t", args_summary: "s", reason: "r", status: "dismissed" },
        }],
        search_result_batches: [[], []],
        routing_decision: null as never,
      },
    ]);
    const msg = useChatStore.getState().sessions[0]!.messages[1]!;
    expect(msg.agent_summary).toEqual({ papers_read: 0, steps: 1, tools: [] });
    expect(msg.agent_events![0]!.data).toMatchObject({ status: "dismissed" });
    expect(msg.search_result_batches).toHaveLength(2);
    expect(msg.routing_decision).toBeUndefined();
  });

  it("mirrors the session's auto_approve from GET /sessions", () => {
    useChatStore.getState().reconcileBackendSessions([
      { id: 5, title: "t", created_at: "", updated_at: "", message_count: 2, auto_approve: true },
    ]);
    expect(useChatStore.getState().sessions[0]!.auto_approve).toBe(true);
  });
});

describe("agent turn replay is idempotent (reattach restarts at cursor 0)", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "t",
          backend_session_id: null,
          messages: [
            { role: "user", content: "q", run_id: null },
            { role: "assistant", content: "", run_id: 7, status: "processing" },
          ],
        },
      ],
      activeSessionId: 1,
    });
  });
  const last = () => useChatStore.getState().sessions[0]!.messages[1]!;
  const cand = (id: string) => ({
    paper_id: id, title: id, authors: [], year: null, abstract: null,
    arxiv_id: null, has_open_pdf: false, reason: "", finalize: false,
    auto_added: false, papers_id: null, error: null, already_in_session: false,
  });
  const events: { event: string; data: string }[] = [
    { event: "search_results", data: JSON.stringify({ run_id: 7, candidates: [cand("a")] }) },
    { event: "search_results", data: JSON.stringify({ run_id: 7, candidates: [cand("b")] }) },
    { event: "paper_added", data: JSON.stringify({ papers_id: 3, paper_content_id: 2, title: "T" }) },
    { event: "memory_saved", data: JSON.stringify({ memory_id: 4, content: "m", scope: "global" }) },
    { event: "confirm_request", data: JSON.stringify({ action_id: "x", tool: "t", args_summary: "s", reason: "r" }) },
  ];

  it("replaying the same events twice adds nothing", () => {
    const s = useChatStore.getState();
    for (const e of events) s.applyRunEvent(1, e);
    for (const e of events) s.applyRunEvent(1, e);
    expect(last().search_result_batches!.map((b) => b.map((c) => c.paper_id))).toEqual([["a"], ["b"]]);
    expect(last().search_results!.map((c) => c.paper_id)).toEqual(["a", "b"]);
    expect(last().agent_events!.map((e) => e.event)).toEqual([
      "paper_added", "memory_saved", "confirm_request",
    ]);
  });

  it("a replayed confirm does not reset a card the user already confirmed", () => {
    const s = useChatStore.getState();
    s.applyRunEvent(1, events[4]!);
    s.patchAgentEvent(1, 7, 0, { status: "done", result: { content: "ok" } });
    s.applyRunEvent(1, events[4]!);
    expect(last().agent_events).toHaveLength(1);
    expect(last().agent_events![0]!.data).toMatchObject({ status: "done" });
  });

  it("answer_reset keeps the trace, cards, batches and summary", () => {
    const s = useChatStore.getState();
    s.appendToken(1, 7, "Let me look.");
    for (const e of events) s.applyRunEvent(1, e);
    s.applyAgentEvent(1, 7, "agent_summary", { papers_read: 1, steps: 2, tools: ["x"] });
    s.appendTrace(1, 7, {
      run_id: 7, branch: "", step_index: 0, parent_step: null, agent: "agent",
      tool: "agent:step", model: null, args_redacted_json: null,
      result_summary_json: null, latency_ms: 1, token_in: null, token_out: null,
      status: "ok", error: null,
    });
    const before = last();
    s.applyAgentEvent(1, 7, "answer_reset", {});
    const after = last();
    expect(after.content).toBe("");
    expect(after.trace).toEqual(before.trace);
    expect(after.agent_events).toEqual(before.agent_events);
    expect(after.search_result_batches).toEqual(before.search_result_batches);
    expect(after.agent_summary).toEqual(before.agent_summary);
  });
});
