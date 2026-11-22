import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { JUST_CREATED_GRACE_MS, useChatStore } from "@/store/chat";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";

let created = 0;
const server = setupServer(
  http.post(`${API_BASE_URL}/sessions`, async () => {
    created += 1;
    await new Promise((r) => setTimeout(r, 20));
    return HttpResponse.json({ session_id: 600 + created }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  clearPendingNoteOps();
  server.resetHandlers();
  vi.useRealTimers();
});
beforeEach(() => {
  created = 0;
  useChatStore.getState().reset();
});

describe("ensureBackendSession (Task 14)", () => {
  it("callers at once share one POST /sessions", async () => {
    const local = useChatStore.getState().newSession();
    const [a, b, c] = await Promise.all([
      useChatStore.getState().ensureBackendSession(local),
      useChatStore.getState().ensureBackendSession(local),
      useChatStore.getState().ensureBackendSession(local),
    ]);
    expect(created).toBe(1);
    expect(new Set([a, b, c])).toEqual(new Set([601]));
    expect(useChatStore.getState().sessions.find((s) => s.id === local)?.backend_session_id).toBe(601);
  });

  it("a chat just put on the server is not pruned before the server lists it", async () => {
    const local = useChatStore.getState().newSession();
    await useChatStore.getState().ensureBackendSession(local);
    // A sync lands before the note's first save: the server lists nothing yet.
    useChatStore.getState().reconcileBackendSessions([]);
    const st = useChatStore.getState();
    expect(st.sessions.some((s) => s.id === local)).toBe(true);
    expect(st.activeSessionId).toBe(local);
  });

  it("past the grace period, a chat the server never lists is pruned as before", async () => {
    const local = useChatStore.getState().newSession();
    await useChatStore.getState().ensureBackendSession(local);
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(Date.now() + JUST_CREATED_GRACE_MS + 1000);
    useChatStore.getState().reconcileBackendSessions([]);
    expect(useChatStore.getState().sessions.some((s) => s.id === local)).toBe(false);
  });

  it("past the grace period, a chat whose note still holds unsaved words stays listed", async () => {
    const local = useChatStore.getState().newSession();
    await useChatStore.getState().ensureBackendSession(local);
    // The note's first save keeps failing: its words wait in the queue.
    const ph = useNotesStore.getState().openChatDraft(local);
    useNotesStore.getState().applyLocal(
      ph,
      [{ op: "insert", block_id: "p1", block: { type: "paragraph", props: {}, content: [{ type: "text", text: "kept", styles: {} }] } }],
      "pane",
    );
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(Date.now() + JUST_CREATED_GRACE_MS + 1000);
    useChatStore.getState().reconcileBackendSessions([]);
    expect(useChatStore.getState().sessions.some((s) => s.id === local)).toBe(true);
    // Once nothing is left unsaved, the strict mirror applies again.
    clearPendingNoteOps();
    useChatStore.getState().reconcileBackendSessions([]);
    expect(useChatStore.getState().sessions.some((s) => s.id === local)).toBe(false);
  });
});
