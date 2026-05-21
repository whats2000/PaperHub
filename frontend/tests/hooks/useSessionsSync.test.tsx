import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { useSessionsSync } from "@/hooks/useSessionsSync";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";

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
});

describe("useSessionsSync", () => {
  it("mirrors the backend session list on mount", async () => {
    server.use(
      http.get(`${API_BASE_URL}/sessions`, () =>
        HttpResponse.json([
          {
            id: 7,
            title: "Flow matching",
            created_at: "t",
            updated_at: "t",
            message_count: 2,
          },
        ]),
      ),
    );

    renderHook(() => useSessionsSync());

    await waitFor(() => {
      expect(
        useChatStore.getState().sessions.some((s) => s.backend_session_id === 7),
      ).toBe(true);
    });
    expect(
      useChatStore.getState().sessions.find((s) => s.backend_session_id === 7)
        ?.title,
    ).toBe("Flow matching");
  });

  it("lazily hydrates the active session's history when it has no messages", async () => {
    // The session must be in the backend list, or strict mirror prunes it
    // before it can be hydrated.
    const listHandler = vi.fn(() =>
      HttpResponse.json([
        {
          id: 7,
          title: "S",
          created_at: "t",
          updated_at: "t",
          message_count: 2,
        },
      ]),
    );
    server.use(
      http.get(`${API_BASE_URL}/sessions`, listHandler),
      http.get(`${API_BASE_URL}/sessions/7/messages`, () =>
        HttpResponse.json([
          { role: "user", content: "hi", run_id: 1, created_at: "t1" },
          { role: "assistant", content: "hello", run_id: 1, created_at: "t2" },
        ]),
      ),
    );

    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: 7 }],
      activeSessionId: 1,
      _nextId: 2,
    });

    renderHook(() => useSessionsSync());

    await waitFor(() => {
      expect(useChatStore.getState().sessions[0]!.messages).toHaveLength(2);
    });
    expect(useChatStore.getState().sessions[0]!.messages[1]!.content).toBe(
      "hello",
    );
  });

  it("does not fetch history for a session without a backend id", async () => {
    const msgHandler = vi.fn(() => HttpResponse.json([]));
    server.use(
      http.get(`${API_BASE_URL}/sessions`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/sessions/:id/messages`, msgHandler),
    );

    useChatStore.setState({
      sessions: [{ id: 1, title: "S", messages: [], backend_session_id: null }],
      activeSessionId: 1,
      _nextId: 2,
    });

    renderHook(() => useSessionsSync());

    await new Promise((r) => setTimeout(r, 30));
    expect(msgHandler).not.toHaveBeenCalled();
  });

  it("does not throw when listing sessions fails", async () => {
    server.use(
      http.get(`${API_BASE_URL}/sessions`, () =>
        HttpResponse.text("boom", { status: 500 }),
      ),
    );
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(() => renderHook(() => useSessionsSync())).not.toThrow();
    await waitFor(() => expect(warnSpy).toHaveBeenCalled());

    warnSpy.mockRestore();
  });
});
