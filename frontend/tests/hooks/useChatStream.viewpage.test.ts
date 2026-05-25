import { renderHook, act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import { API_BASE_URL } from "@/lib/api";
import { chitchatHappyPath } from "../stubs/sse";
import type { DeckEventData } from "@/types/domain";

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

/** A canned slides-intent stream that also captures the POSTed request body. */
function captureBodyHandler(captured: { body?: Record<string, unknown> }) {
  return http.post(`${API_BASE_URL}/chat`, async ({ request }) => {
    captured.body = (await request.json()) as Record<string, unknown>;
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(chunk("session", { run_id: 1, session_id: 11 }));
        controller.enqueue(
          chunk("routing_decision", {
            run_id: 1,
            branch: "",
            decision: {
              intent: "slides",
              model_tier: "flagship",
              confidence: 0.95,
              reasoning: "x",
            },
          }),
        );
        controller.enqueue(
          chunk("final", {
            run_id: 1,
            branch: "",
            message_id: 1,
            content: "done",
          }),
        );
        controller.close();
      },
    });
    return new HttpResponse(stream, {
      headers: { "Content-Type": "text/event-stream" },
    });
  });
}

const fakeDeck: DeckEventData = {
  deck_id: 5,
  session_id: 11,
  page_count: 8,
  title: "My Deck",
  status: "ok",
  contributing_papers: [],
  has_notes: true,
};

describe("useChatStream current_view_page threading", () => {
  it("includes current_view_page when this session has a deck open", async () => {
    const captured: { body?: Record<string, unknown> } = {};
    server.resetHandlers(captureBodyHandler(captured));

    const sessionId = useChatStore.getState().newSession();
    // Establish the backend_session_id so deckBySession can be keyed by it.
    useChatStore.getState().patchSessionBackendId(sessionId, 11);
    useSlidesStore.getState().setDeck(11, fakeDeck);
    useSlidesStore.getState().setCurrentPage(11, 4);

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send(sessionId, "edit this slide");
    });

    await waitFor(() => {
      expect(captured.body).toBeDefined();
    });
    expect(captured.body!.current_view_page).toBe(4);
  });

  it("omits current_view_page when this session has no deck", async () => {
    const captured: { body?: Record<string, unknown> } = {};
    server.resetHandlers(captureBodyHandler(captured));

    const sessionId = useChatStore.getState().newSession();
    useChatStore.getState().patchSessionBackendId(sessionId, 11);
    // No deck set for session 11.

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send(sessionId, "hello");
    });

    await waitFor(() => {
      expect(captured.body).toBeDefined();
    });
    expect("current_view_page" in captured.body!).toBe(false);
  });
});
