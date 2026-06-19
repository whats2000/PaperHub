import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "@/store/chat";

beforeEach(() => {
  // Clear persisted storage and reset store between tests
  localStorage.clear();
  useChatStore.getState().reset();
});

describe("chat store", () => {
  it("starts with no active session", () => {
    expect(useChatStore.getState().activeSessionId).toBeNull();
  });

  it("requestComposerText prefills the draft and bumps the focus signal", () => {
    const before = useChatStore.getState().composerFocusSeq;
    useChatStore.getState().requestComposerText("Edit this slide: ");
    const s = useChatStore.getState();
    expect(s.composerDraft).toBe("Edit this slide: ");
    expect(s.composerFocusSeq).toBe(before + 1);
  });

  it("creates a new session and selects it", () => {
    const id = useChatStore.getState().newSession();
    expect(id).toBeGreaterThan(0);
    expect(useChatStore.getState().activeSessionId).toBe(id);
  });

  it("appends a user message to the active session", () => {
    const id = useChatStore.getState().newSession();
    useChatStore.getState().appendMessage(id, {
      role: "user",
      content: "hello",
      run_id: null,
    });
    const session = useChatStore.getState().sessions.find((s) => s.id === id);
    expect(session).toBeDefined();
    expect(session!.messages).toHaveLength(1);
    expect(session!.messages[0]!.content).toBe("hello");
  });

  it("auto-derives session title from first user message", () => {
    const id = useChatStore.getState().newSession();
    useChatStore.getState().appendMessage(id, {
      role: "user",
      content: "Find papers on mixture-of-experts routing",
      run_id: null,
    });
    const session = useChatStore.getState().sessions.find((s) => s.id === id);
    expect(session!.title).not.toBe("New chat");
    expect(session!.title).toContain("Find papers");
  });

  it("truncates long first user message to 40 chars with ellipsis", () => {
    const id = useChatStore.getState().newSession();
    useChatStore.getState().appendMessage(id, {
      role: "user",
      content: "This is a very long message that exceeds forty characters in length",
      run_id: null,
    });
    const session = useChatStore.getState().sessions.find((s) => s.id === id);
    expect(session!.title.length).toBeLessThanOrEqual(41); // 40 chars + "…"
    expect(session!.title).toMatch(/…$/);
  });

  it("preserves title when subsequent user messages arrive", () => {
    const id = useChatStore.getState().newSession();
    useChatStore.getState().appendMessage(id, {
      role: "user",
      content: "First message",
      run_id: null,
    });
    const titleAfterFirst = useChatStore
      .getState()
      .sessions.find((s) => s.id === id)!.title;
    useChatStore.getState().appendMessage(id, {
      role: "user",
      content: "Second message that should not change the title",
      run_id: null,
    });
    const session = useChatStore.getState().sessions.find((s) => s.id === id);
    expect(session!.title).toBe(titleAfterFirst);
  });

  it("deleteSession + restoreSession reinserts at the original index", () => {
    const idA = useChatStore.getState().newSession();
    const idB = useChatStore.getState().newSession();
    const idC = useChatStore.getState().newSession();

    // Sessions are [A, B, C], B is at index 1
    const stateBeforeDelete = useChatStore.getState().sessions;
    const idxB = stateBeforeDelete.findIndex((s) => s.id === idB);
    expect(idxB).toBe(1);

    const removed = useChatStore.getState().deleteSession(idB);
    expect(removed).not.toBeNull();
    expect(useChatStore.getState().sessions).toHaveLength(2);

    useChatStore.getState().restoreSession(removed!, idxB);
    const sessions = useChatStore.getState().sessions;
    expect(sessions).toHaveLength(3);
    expect(sessions[1]!.id).toBe(idB);
    // Verify the other sessions are still in order
    expect(sessions[0]!.id).toBe(idA);
    expect(sessions[2]!.id).toBe(idC);
  });

  it("deleteSession clears activeSessionId when active session is deleted", () => {
    const id = useChatStore.getState().newSession();
    expect(useChatStore.getState().activeSessionId).toBe(id);
    useChatStore.getState().deleteSession(id);
    expect(useChatStore.getState().activeSessionId).toBeNull();
  });

  it("persist writes to localStorage after a write operation", () => {
    useChatStore.getState().newSession();
    // Zustand persist writes synchronously on set()
    const raw = localStorage.getItem("paperhub-chat-v1");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!) as { state: { sessions: unknown[] } };
    expect(parsed.state.sessions).toHaveLength(1);
  });

  it("setComposerDraft updates composerDraft", () => {
    expect(useChatStore.getState().composerDraft).toBe("");
    useChatStore.getState().setComposerDraft("Find papers on transformers");
    expect(useChatStore.getState().composerDraft).toBe(
      "Find papers on transformers",
    );
  });

  it("toggleSidebar flips sidebarCollapsed", () => {
    expect(useChatStore.getState().sidebarCollapsed).toBe(false);
    useChatStore.getState().toggleSidebar();
    expect(useChatStore.getState().sidebarCollapsed).toBe(true);
    useChatStore.getState().toggleSidebar();
    expect(useChatStore.getState().sidebarCollapsed).toBe(false);
  });

  it("removeMessage removes the message at the specified index", () => {
    const id = useChatStore.getState().newSession();
    useChatStore.getState().appendMessage(id, {
      role: "user", content: "msg 0", run_id: null,
    });
    useChatStore.getState().appendMessage(id, {
      role: "assistant", content: "msg 1", run_id: 1, status: "ok",
    });
    useChatStore.getState().appendMessage(id, {
      role: "user", content: "msg 2", run_id: null,
    });

    useChatStore.getState().removeMessage(id, 1);

    const session = useChatStore.getState().sessions.find((s) => s.id === id)!;
    expect(session.messages).toHaveLength(2);
    expect(session.messages[0]!.content).toBe("msg 0");
    expect(session.messages[1]!.content).toBe("msg 2");
  });

  // -------------------------------------------------------------------------
  // appendTrace — dedup real steps; collapse + strip live heartbeats
  // -------------------------------------------------------------------------
  const rec = (
    step_index: number,
    tool: string,
    result: Record<string, unknown> | null = null,
  ) => ({
    run_id: 1, branch: "" as const, step_index, parent_step: null,
    agent: "report", tool, model: null,
    args_redacted_json: null, result_summary_json: result,
    latency_ms: 0, token_in: null, token_out: null,
    status: "ok" as const, error: null,
  });

  const seedAssistant = (): number => {
    const id = useChatStore.getState().newSession();
    useChatStore.getState().appendMessage(id, {
      role: "assistant", content: "", run_id: 1, status: "streaming",
    });
    return id;
  };

  const traceOf = (id: number) =>
    useChatStore.getState().sessions.find((s) => s.id === id)!
      .messages.find((m) => m.run_id === 1)!.trace ?? [];

  it("appendTrace dedups a re-emitted real step by step_index", () => {
    const id = seedAssistant();
    useChatStore.getState().appendTrace(id, 1, rec(0, "classify"));
    useChatStore.getState().appendTrace(id, 1, rec(1, "detect_language"));
    // The report graph's per-node flush re-emits step 0 — must NOT stack.
    useChatStore.getState().appendTrace(id, 1, rec(0, "classify"));
    const reals = traceOf(id).filter((r) => r.step_index >= 0);
    expect(reals.map((r) => r.step_index)).toEqual([0, 1]);
  });

  it("appendTrace collapses heartbeat beats into a single live marker", () => {
    const id = seedAssistant();
    useChatStore.getState().appendTrace(id, 1, rec(-1, "report:planning", { stage: true, elapsed_s: 0 }));
    useChatStore.getState().appendTrace(id, 1, rec(-2, "report:planning", { stage: true, elapsed_s: 15 }));
    useChatStore.getState().appendTrace(id, 1, rec(-3, "report:planning", { stage: true, elapsed_s: 30 }));
    const beats = traceOf(id).filter((r) => r.step_index < 0);
    expect(beats).toHaveLength(1);
    expect(beats[0]!.result_summary_json!.elapsed_s).toBe(30); // latest beat wins
  });

  it("appendTrace keeps the live beat after a real step; finalize strips it", () => {
    const id = seedAssistant();
    useChatStore.getState().appendTrace(id, 1, rec(0, "classify"));
    useChatStore.getState().appendTrace(id, 1, rec(-1, "report:planning", { stage: true, elapsed_s: 0 }));
    expect(traceOf(id).filter((r) => r.step_index < 0)).toHaveLength(1);
    useChatStore.getState().finaliseMessage(id, 1, "done");
    const after = traceOf(id);
    expect(after.filter((r) => r.step_index < 0)).toHaveLength(0); // beat stripped
    expect(after.filter((r) => r.step_index >= 0)).toHaveLength(1); // step kept
  });

  // -------------------------------------------------------------------------
  // applyRunEvent — tool_step unwrap regression (FR-15 A10)
  // -------------------------------------------------------------------------
  it("applyRunEvent tool_step: unwraps data.record and appends trace (reattach fidelity)", () => {
    // The SSE wire shape for tool_step is { record: ToolCallRecord }, NOT flat.
    // applyRunEvent must unwrap data.record — previously it read data.run_id
    // (undefined) and silently dropped the event; this test catches that regression.
    const id = seedAssistant(); // assistant message with run_id=1 already present
    const toolStepPayload = JSON.stringify({
      record: {
        run_id: 1,
        branch: "",
        step_index: 0,
        parent_step: null,
        agent: "research",
        tool: "paper_qa:subagent",
        model: null,
        args_redacted_json: null,
        result_summary_json: { chunks_cited: [42] },
        latency_ms: 123,
        token_in: null,
        token_out: null,
        status: "ok",
        error: null,
      },
    });
    useChatStore.getState().applyRunEvent(id, { event: "tool_step", data: toolStepPayload });
    const trace = traceOf(id);
    expect(trace).toHaveLength(1);
    expect(trace[0]!.tool).toBe("paper_qa:subagent");
    expect(trace[0]!.step_index).toBe(0);
    expect(trace[0]!.run_id).toBe(1);
  });

  it("applyRunEvent tool_step: no-ops when record is absent (malformed event)", () => {
    // A flat (un-wrapped) payload must not crash and must leave the trace empty.
    const id = seedAssistant();
    const flatPayload = JSON.stringify({
      run_id: 1, step_index: 0, tool: "paper_qa:subagent",
    });
    useChatStore.getState().applyRunEvent(id, { event: "tool_step", data: flatPayload });
    expect(traceOf(id)).toHaveLength(0);
  });
});
