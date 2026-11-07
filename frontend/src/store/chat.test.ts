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
