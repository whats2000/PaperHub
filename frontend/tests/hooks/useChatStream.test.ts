import { renderHook, act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import { API_BASE_URL } from "@/lib/api";
import { chitchatHappyPath } from "../stubs/sse";

const server = setupServer(chitchatHappyPath);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  server.resetHandlers(chitchatHappyPath);
  useChatStore.getState().reset();
  useSlidesStore.setState({ open: false, deckBySession: {}, currentPageBySession: {} });
});

const enc = new TextEncoder();
function chunk(event: string, data: unknown): Uint8Array {
  return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

const midStreamFailure = http.post(`${API_BASE_URL}/chat`, () => {
  const stream = new ReadableStream({
    start(controller) {
      // Enqueue the two pre-error events synchronously so the reader can pull them.
      controller.enqueue(
        chunk("tool_step", {
          record: {
            run_id: 7, branch: "", step_index: 0, agent: "router",
            tool: "classify", model: "x", latency_ms: 12, status: "ok",
            parent_step: null, args_redacted_json: null,
            result_summary_json: null, token_in: null, token_out: null,
            error: null,
          },
        }),
      );
      controller.enqueue(
        chunk("routing_decision", {
          run_id: 7, branch: "",
          decision: {
            intent: "chitchat", model_tier: "small",
            confidence: 0.9, reasoning: "x",
          },
        }),
      );
      // Defer the error so the reader processes the queued chunks first,
      // then sees the stream abort mid-flight (simulating a network blip).
      setTimeout(() => controller.error(new Error("network blip")), 10);
    },
  });
  return new HttpResponse(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
});

describe("useChatStream", () => {
  it("runs a chitchat round-trip and updates the store", async () => {
    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send(sessionId, "hello");
    });

    await waitFor(() => {
      const session = useChatStore.getState().sessions.find((s) => s.id === sessionId);
      expect(session).toBeDefined();
      const assistant = session!.messages.find((m) => m.role === "assistant");
      expect(assistant).toBeDefined();
      expect(assistant!.status).toBe("ok");
      expect(assistant!.content).toBe("Hi there!");
      expect(assistant!.routing_decision?.intent).toBe("chitchat");
      expect(assistant!.trace).toHaveLength(1);
    });
  });

  it("flips the streaming placeholder to error when SSE fails before any event", async () => {
    server.resetHandlers(
      http.post(`${API_BASE_URL}/chat`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    let threw = false;
    await act(async () => {
      try {
        await result.current.send(sessionId, "hello");
      } catch {
        threw = true;
      }
    });

    expect(threw).toBe(true); // pre-event failures DO propagate to caller (→ toast)

    await waitFor(() => {
      const session = useChatStore.getState().sessions.find((s) => s.id === sessionId);
      const assistant = session!.messages.find((m) => m.role === "assistant")!;
      expect(assistant.status).toBe("error");
      expect(assistant.error).toBeTruthy();
    });
  });

  it("mid-stream failure: inline error only, no re-throw", async () => {
    server.resetHandlers(midStreamFailure);
    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    let threw = false;
    await act(async () => {
      try {
        await result.current.send(sessionId, "hello");
      } catch {
        threw = true;
      }
    });

    expect(threw).toBe(false); // mid-stream errors must NOT propagate

    await waitFor(() => {
      const session = useChatStore.getState().sessions.find((s) => s.id === sessionId);
      const assistant = session!.messages.find((m) => m.role === "assistant")!;
      expect(assistant.status).toBe("error");
      expect(assistant.error).toBeTruthy();
      // The run_id was patched from the tool_step before the failure
      expect(assistant.run_id).toBe(7);
    });
  });

  it("second message includes prior turns as history", async () => {
    // Capture what the server received on the second request
    let capturedHistory: unknown = undefined;

    // First request: happy path (uses chitchatHappyPath which sends run_id: 1)
    // Second request: captures the body and responds with a canned SSE stream
    let requestCount = 0;
    server.resetHandlers(
      http.post(`${API_BASE_URL}/chat`, async ({ request }) => {
        requestCount += 1;
        const body = await request.json() as { history: unknown };
        if (requestCount === 2) {
          capturedHistory = body.history;
        }
        const enc2 = new TextEncoder();
        function sseChunk2(event: string, data: unknown): Uint8Array {
          return enc2.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
        }
        const runId = requestCount;
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(
              sseChunk2("routing_decision", {
                run_id: runId, branch: "",
                decision: { intent: "chitchat", model_tier: "small", confidence: 0.9, reasoning: "x" },
              }),
            );
            controller.enqueue(sseChunk2("token", { run_id: runId, branch: "", text: "A reply" }));
            controller.enqueue(
              sseChunk2("final", { run_id: runId, branch: "", message_id: runId, content: "A reply" }),
            );
            controller.close();
          },
        });
        return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
      }),
    );

    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    // First turn: "A"
    await act(async () => {
      await result.current.send(sessionId, "A");
    });

    await waitFor(() => {
      const session = useChatStore.getState().sessions.find((s) => s.id === sessionId);
      const assistant = session!.messages.find((m) => m.role === "assistant");
      expect(assistant?.status).toBe("ok");
    });

    // Second turn: "B" — the hook must include prior turns as history
    await act(async () => {
      await result.current.send(sessionId, "B");
    });

    await waitFor(() => {
      const session = useChatStore.getState().sessions.find((s) => s.id === sessionId);
      const messages = session!.messages;
      expect(messages.filter((m) => m.role === "assistant").every((m) => m.status === "ok")).toBe(true);
    });

    expect(capturedHistory).toEqual([
      { role: "user", content: "A" },
      { role: "assistant", content: "A reply" },
    ]);
  });

  it("stores backend_session_id from session event and sends it on subsequent turns", async () => {
    // Capture the session_id the client sends on each request
    const capturedSessionIds: (number | null)[] = [];

    let requestCount = 0;
    server.resetHandlers(
      http.post(`${API_BASE_URL}/chat`, async ({ request }) => {
        requestCount += 1;
        const body = await request.json() as { session_id: number | null };
        capturedSessionIds.push(body.session_id);

        const enc2 = new TextEncoder();
        function sseChunk2(event: string, data: unknown): Uint8Array {
          return enc2.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
        }
        const runId = requestCount;
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(
              sseChunk2("session", { run_id: runId, session_id: 42 }),
            );
            controller.enqueue(
              sseChunk2("routing_decision", {
                run_id: runId, branch: "",
                decision: { intent: "chitchat", model_tier: "small", confidence: 0.9, reasoning: "x" },
              }),
            );
            controller.enqueue(sseChunk2("token", { run_id: runId, branch: "", text: "Reply" }));
            controller.enqueue(
              sseChunk2("final", { run_id: runId, branch: "", message_id: runId, content: "Reply" }),
            );
            controller.close();
          },
        });
        return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
      }),
    );

    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    // First turn: session_id should be null (no backend session yet)
    await act(async () => {
      await result.current.send(sessionId, "first");
    });

    await waitFor(() => {
      const session = useChatStore.getState().sessions.find((s) => s.id === sessionId);
      expect(session?.backend_session_id).toBe(42);
    });

    // Second turn: session_id should be 42 (learned from first session event)
    await act(async () => {
      await result.current.send(sessionId, "second");
    });

    await waitFor(() => {
      const session = useChatStore.getState().sessions.find((s) => s.id === sessionId);
      const messages = session!.messages.filter((m) => m.role === "assistant");
      expect(messages.every((m) => m.status === "ok")).toBe(true);
    });

    expect(capturedSessionIds[0]).toBeNull();
    expect(capturedSessionIds[1]).toBe(42);
    // backend_session_id must stay 42 (idempotent — not overwritten on second turn)
    const finalSession = useChatStore.getState().sessions.find((s) => s.id === sessionId);
    expect(finalSession?.backend_session_id).toBe(42);
  });

  it("dispatches deck SSE event into chat store and slides store", async () => {
    const deckPayload = {
      deck_id: 5,
      session_id: 11,
      page_count: 8,
      title: "My Deck",
      status: "ok" as const,
      contributing_papers: [{ id: 1, title: "Paper A" }],
      has_notes: true,
    };
    server.resetHandlers(
      http.post(`${API_BASE_URL}/chat`, () => {
        const enc2 = new TextEncoder();
        function sseChunk(event: string, data: unknown): Uint8Array {
          return enc2.encode(
            `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
          );
        }
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(
              sseChunk("session", { run_id: 1, session_id: 11 }),
            );
            controller.enqueue(
              sseChunk("routing_decision", {
                run_id: 1, branch: "",
                decision: {
                  intent: "slides", model_tier: "flagship",
                  confidence: 0.95, reasoning: "generate slides",
                },
              }),
            );
            controller.enqueue(sseChunk("deck", deckPayload));
            controller.enqueue(
              sseChunk("final", {
                run_id: 1, branch: "", message_id: 1,
                content: "Your slides are ready.",
              }),
            );
            controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );

    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send(sessionId, "generate slides");
    });

    await waitFor(() => {
      // Chat store: the assistant message should have deck attached
      const session = useChatStore
        .getState()
        .sessions.find((s) => s.id === sessionId);
      const assistant = session!.messages.find((m) => m.role === "assistant")!;
      expect(assistant.status).toBe("ok");
      expect(assistant.deck).toBeDefined();
      expect(assistant.deck!.deck_id).toBe(5);
      expect(assistant.deck!.page_count).toBe(8);
      expect(assistant.deck!.title).toBe("My Deck");

      // Slides store: deckBySession and currentPageBySession updated
      const slidesState = useSlidesStore.getState();
      expect(slidesState.deckBySession[11]).toBeDefined();
      expect(slidesState.deckBySession[11]!.deck_id).toBe(5);
      expect(slidesState.currentPageBySession[11]).toBe(1);
    });
  });

  it("dispatches search_results SSE event into the chat store", async () => {
    const candidates = [
      {
        paper_id: "ss:abcd",
        title: "Mamba",
        authors: ["Alice"],
        year: 2024,
        abstract: "state-space",
        arxiv_id: "2312.00752",
        has_open_pdf: true,
        reason: "headline 2024 work",
        finalize: true,
        auto_added: true,
        papers_id: 7,
        error: null,
        already_in_session: false,
      },
      {
        paper_id: "ss:efgh",
        title: "Another",
        authors: [],
        year: 2024,
        abstract: "abs",
        arxiv_id: null,
        has_open_pdf: false,
        reason: "tangential",
        finalize: false,
        auto_added: false,
        papers_id: null,
        error: null,
        already_in_session: false,
      },
    ];
    server.resetHandlers(
      http.post(`${API_BASE_URL}/chat`, () => {
        const enc2 = new TextEncoder();
        function sseChunk(event: string, data: unknown): Uint8Array {
          return enc2.encode(
            `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
          );
        }
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(
              sseChunk("session", { run_id: 1, session_id: 11 }),
            );
            controller.enqueue(
              sseChunk("routing_decision", {
                run_id: 1, branch: "",
                decision: {
                  intent: "paper_search", model_tier: "flagship",
                  confidence: 0.95, reasoning: "find papers",
                },
              }),
            );
            controller.enqueue(
              sseChunk("search_results", { run_id: 1, candidates }),
            );
            controller.enqueue(
              sseChunk("final", {
                run_id: 1, branch: "", message_id: 1,
                content: "Here are picks.",
              }),
            );
            controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );

    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send(sessionId, "find papers");
    });

    await waitFor(() => {
      const session = useChatStore
        .getState()
        .sessions.find((s) => s.id === sessionId);
      const assistant = session!.messages.find((m) => m.role === "assistant")!;
      expect(assistant.status).toBe("ok");
      const results = assistant.search_results;
      expect(results).toBeDefined();
      expect(results).toHaveLength(2);
      expect(results![0]!.auto_added).toBe(true);
      expect(results![0]!.papers_id).toBe(7);
      expect(results![1]!.finalize).toBe(false);
    });
  });
});
