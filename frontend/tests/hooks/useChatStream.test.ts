import { renderHook, act, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { chitchatHappyPath } from "../stubs/sse";

const server = setupServer(chitchatHappyPath);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  server.resetHandlers(chitchatHappyPath);
  useChatStore.getState().reset();
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
});
