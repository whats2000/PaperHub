import { renderHook, act } from "@testing-library/react";
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

import { useRunReattach } from "@/hooks/useRunReattach";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import type { BackendMessage } from "@/types/domain";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  server.resetHandlers();
  vi.useRealTimers();
});

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

/** Helpers to build MSW responses */
function eventsResponse(
  status: string,
  events: { event: string; data: string }[],
  nextCursor: number,
) {
  return HttpResponse.json({ status, events, next_cursor: nextCursor });
}

function backendMessages(msgs: Partial<BackendMessage>[]): BackendMessage[] {
  return msgs.map((m): BackendMessage => ({
    role: m.role ?? "assistant",
    content: m.content ?? "",
    run_id: m.run_id ?? null,
    created_at: "2026-06-18T00:00:00Z",
    ...m,
  }));
}

describe("useRunReattach", () => {
  it("does nothing when there is no active session", async () => {
    const handler = vi.fn(() => HttpResponse.json({ status: "ok", events: [], next_cursor: 0 }));
    server.use(http.get(`${API_BASE_URL}/chat/runs/:id/events`, handler));

    renderHook(() => useRunReattach());

    await new Promise((r) => setTimeout(r, 30));
    expect(handler).not.toHaveBeenCalled();
  });

  it("does nothing when trailing message is not processing", async () => {
    const handler = vi.fn(() => HttpResponse.json({ status: "ok", events: [], next_cursor: 0 }));
    server.use(http.get(`${API_BASE_URL}/chat/runs/:id/events`, handler));

    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "S1",
          messages: [
            { role: "user", content: "hi", run_id: null },
            { role: "assistant", content: "hello", run_id: 10, status: "ok" },
          ],
          backend_session_id: 5,
        },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
    });

    renderHook(() => useRunReattach());

    await new Promise((r) => setTimeout(r, 30));
    expect(handler).not.toHaveBeenCalled();
  });

  it("polls events, fills the bubble via token, then stops on terminal ok", async () => {
    vi.useFakeTimers();

    let pollCount = 0;
    server.use(
      http.get(`${API_BASE_URL}/chat/runs/42/events`, () => {
        pollCount++;
        if (pollCount === 1) {
          // First poll: return a token event, non-terminal
          return eventsResponse(
            "running",
            [{ event: "token", data: JSON.stringify({ run_id: 42, text: "hello " }) }],
            1,
          );
        }
        // Second poll: terminal ok with final event
        return eventsResponse(
          "ok",
          [{ event: "final", data: JSON.stringify({ run_id: 42, content: "hello world" }) }],
          2,
        );
      }),
      // Settle refetch after terminal
      http.get(`${API_BASE_URL}/sessions/5/messages`, () =>
        HttpResponse.json(
          backendMessages([
            { role: "user", content: "hi", run_id: 42 },
            { role: "assistant", content: "hello world", run_id: 42 },
          ]),
        ),
      ),
    );

    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "S1",
          messages: [
            { role: "user", content: "hi", run_id: 42 },
            { role: "assistant", content: "", run_id: 42, status: "processing" },
          ],
          backend_session_id: 5,
        },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
    });

    renderHook(() => useRunReattach());

    // First poll fires immediately on mount — flush it
    await act(async () => {
      // Let the immediate poll's async operations (fetch + applyRunEvent) complete
      await vi.advanceTimersByTimeAsync(50);
    });

    // After first poll (token), content should be "hello "
    const msgs1 = useChatStore.getState().sessions[0]?.messages ?? [];
    const assistant1 = msgs1.find((m) => m.role === "assistant" && m.run_id === 42);
    expect(assistant1?.content).toBe("hello ");

    // Advance timer to trigger second poll (1000ms interval)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    // After terminal response + settle refetch, message should be ok
    const msgs2 = useChatStore.getState().sessions[0]?.messages ?? [];
    const assistant2 = msgs2.find((m) => m.role === "assistant" && m.run_id === 42);
    expect(assistant2?.status).toBe("ok");
    expect(assistant2?.content).toBe("hello world");

    // Further timer advances should NOT trigger more polls
    const pollCountBeforeExtra = pollCount;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(pollCount).toBe(pollCountBeforeExtra);
  });

  it("removes the processing placeholder on cancelled status", async () => {
    vi.useFakeTimers();

    server.use(
      http.get(`${API_BASE_URL}/chat/runs/77/events`, () =>
        eventsResponse("cancelled", [], 0),
      ),
      // Settle refetch returns only the user message (cancelled = placeholder gone)
      http.get(`${API_BASE_URL}/sessions/9/messages`, () =>
        HttpResponse.json(
          backendMessages([
            { role: "user", content: "do stuff", run_id: 77 },
          ]),
        ),
      ),
    );

    useChatStore.setState({
      sessions: [
        {
          id: 2,
          title: "S2",
          messages: [
            { role: "user", content: "do stuff", run_id: 77 },
            { role: "assistant", content: "", run_id: 77, status: "processing" },
          ],
          backend_session_id: 9,
        },
      ],
      activeSessionId: 2,
      _nextId: 3,
      referencesBySession: {},
    });

    renderHook(() => useRunReattach());

    // Let the poll + settle complete
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });

    // After cancelled + settle refetch, hydrateSessionMessages was called with
    // just the user message. No assistant follows a non-running user, so no
    // processing placeholder is added. The processing placeholder is gone.
    const msgs = useChatStore.getState().sessions.find((s) => s.id === 2)?.messages ?? [];
    expect(msgs.find((m) => m.role === "assistant" && m.status === "processing")).toBeUndefined();
  });

  it("swallows fetch errors and retries on next tick", async () => {
    vi.useFakeTimers();

    let callCount = 0;
    server.use(
      http.get(`${API_BASE_URL}/chat/runs/55/events`, () => {
        callCount++;
        if (callCount === 1) {
          return HttpResponse.text("server error", { status: 500 });
        }
        // Subsequent calls succeed with non-terminal, no events
        return eventsResponse("running", [], callCount);
      }),
    );

    useChatStore.setState({
      sessions: [
        {
          id: 3,
          title: "S3",
          messages: [
            { role: "user", content: "hello", run_id: 55 },
            { role: "assistant", content: "", run_id: 55, status: "processing" },
          ],
          backend_session_id: 11,
        },
      ],
      activeSessionId: 3,
      _nextId: 4,
      referencesBySession: {},
    });

    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    renderHook(() => useRunReattach());

    // First call fails — let it complete
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });

    // Status should still be processing (error swallowed)
    const msgs = useChatStore.getState().sessions.find((s) => s.id === 3)?.messages ?? [];
    expect(msgs.find((m) => m.role === "assistant")?.status).toBe("processing");

    // Second call succeeds (advances cursor, still running) — advance interval
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    expect(callCount).toBeGreaterThanOrEqual(2);

    warnSpy.mockRestore();
  });

  it("stops polling on session switch", async () => {
    vi.useFakeTimers();

    let callCount = 0;
    server.use(
      http.get(`${API_BASE_URL}/chat/runs/66/events`, () => {
        callCount++;
        return eventsResponse("running", [], callCount);
      }),
    );

    useChatStore.setState({
      sessions: [
        {
          id: 4,
          title: "S4",
          messages: [
            { role: "user", content: "test", run_id: 66 },
            { role: "assistant", content: "", run_id: 66, status: "processing" },
          ],
          backend_session_id: 12,
        },
        {
          id: 5,
          title: "S5",
          messages: [],
          backend_session_id: 13,
        },
      ],
      activeSessionId: 4,
      _nextId: 6,
      referencesBySession: {},
    });

    renderHook(() => useRunReattach());

    // First poll fires on mount
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });

    const countAfterFirstPoll = callCount;
    expect(countAfterFirstPoll).toBeGreaterThanOrEqual(1);

    // Switch sessions — effect should re-run (new activeSessionId + no processing run)
    // which cleans up the previous interval.
    act(() => {
      useChatStore.setState({ activeSessionId: 5 });
    });

    // Advance time — no more polls for session 4's run since interval was cleared
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(callCount).toBe(countAfterFirstPoll);
  });
});
