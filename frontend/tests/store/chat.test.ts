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
});
