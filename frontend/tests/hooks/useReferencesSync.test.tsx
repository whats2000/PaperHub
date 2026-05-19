import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { useReferencesSync } from "@/hooks/useReferencesSync";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import type { ReferenceItem } from "@/types/domain";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

afterEach(() => {
  server.resetHandlers();
});

beforeEach(() => {
  // Wipe localStorage so the persist middleware doesn't leak between tests.
  localStorage.clear();
  useChatStore.setState({
    sessions: [],
    activeSessionId: null,
    _nextId: 1,
    referencesBySession: {},
    composerDraft: "",
  });
});

function makeRef(papersId: number, title: string): ReferenceItem {
  return {
    papers_id: papersId,
    paper_content_id: papersId * 10,
    enabled: true,
    added_at: "2026-05-20T00:00:00Z",
    arxiv_id: null,
    title,
    year: 2026,
    kind: "arxiv",
  };
}

describe("useReferencesSync", () => {
  it("refetches when the active session changes (1 → 2)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers`, ({ request }) => {
        const url = new URL(request.url);
        const sid = url.searchParams.get("session_id");
        if (sid === "7") {
          return HttpResponse.json([makeRef(1, "Paper for backend 7")]);
        }
        if (sid === "8") {
          return HttpResponse.json([
            makeRef(2, "Paper A for backend 8"),
            makeRef(3, "Paper B for backend 8"),
          ]);
        }
        return HttpResponse.json([]);
      }),
    );

    useChatStore.setState({
      sessions: [
        { id: 1, title: "S1", messages: [], backend_session_id: 7 },
        { id: 2, title: "S2", messages: [], backend_session_id: 8 },
      ],
      activeSessionId: 1,
      _nextId: 3,
      referencesBySession: {},
    });

    renderHook(() => useReferencesSync());

    await waitFor(() => {
      expect(useChatStore.getState().referencesBySession[7]).toHaveLength(1);
    });
    expect(useChatStore.getState().referencesBySession[7]?.[0]?.title).toBe(
      "Paper for backend 7",
    );

    // Switch active session — should trigger another fetch.
    useChatStore.setState({ activeSessionId: 2 });

    await waitFor(() => {
      expect(useChatStore.getState().referencesBySession[8]).toHaveLength(2);
    });
    // First session's bucket should still be intact.
    expect(useChatStore.getState().referencesBySession[7]).toHaveLength(1);
  });

  it("fires a fetch once backend_session_id transitions from null to a number", async () => {
    const handler = vi.fn(() => HttpResponse.json([makeRef(9, "Late ref")]));
    server.use(http.get(`${API_BASE_URL}/papers`, handler));

    useChatStore.setState({
      sessions: [
        { id: 1, title: "S1", messages: [], backend_session_id: null },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
    });

    renderHook(() => useReferencesSync());

    // No backend id yet → no fetch.
    expect(handler).not.toHaveBeenCalled();

    // Backend learns a session id (simulating SSE `session` event).
    useChatStore.getState().patchSessionBackendId(1, 7);

    await waitFor(() => {
      expect(handler).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(useChatStore.getState().referencesBySession[7]).toHaveLength(1);
    });
  });

  it("does not fetch when backend_session_id is null on mount", async () => {
    const handler = vi.fn(() => HttpResponse.json([]));
    server.use(http.get(`${API_BASE_URL}/papers`, handler));

    useChatStore.setState({
      sessions: [
        { id: 1, title: "S1", messages: [], backend_session_id: null },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
    });

    renderHook(() => useReferencesSync());

    // Wait a tick to confirm no fetch fires.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(handler).toHaveBeenCalledTimes(0);
  });

  it("replaces (not merges) the stale cached entry for the session", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers`, () =>
        HttpResponse.json([makeRef(2, "Fresh from backend")]),
      ),
    );

    // Pre-populate the persisted-cache shape with a stale entry that the
    // backend will not return.
    useChatStore.setState({
      sessions: [
        { id: 1, title: "S1", messages: [], backend_session_id: 7 },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {
        7: [makeRef(999, "Ghost ref — deleted upstream")],
      },
    });

    renderHook(() => useReferencesSync());

    await waitFor(() => {
      const refs = useChatStore.getState().referencesBySession[7] ?? [];
      expect(refs).toHaveLength(1);
      expect(refs[0]?.papers_id).toBe(2);
    });
    // Ghost entry must be gone.
    const finalRefs = useChatStore.getState().referencesBySession[7] ?? [];
    expect(finalRefs.find((r) => r.papers_id === 999)).toBeUndefined();
  });

  it("does not throw and preserves prior state on 500", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers`, () =>
        HttpResponse.text("boom", { status: 500 }),
      ),
    );

    const priorRefs = [makeRef(5, "Previously cached")];
    useChatStore.setState({
      sessions: [
        { id: 1, title: "S1", messages: [], backend_session_id: 7 },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: { 7: priorRefs },
    });

    // Silence the expected console.warn.
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(() => renderHook(() => useReferencesSync())).not.toThrow();

    // Give the rejected promise a chance to settle and warn.
    await waitFor(() => {
      expect(warnSpy).toHaveBeenCalled();
    });

    // Prior cached state should be untouched.
    expect(useChatStore.getState().referencesBySession[7]).toEqual(priorRefs);

    warnSpy.mockRestore();
  });
});
