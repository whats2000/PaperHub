import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";

const streamChat = vi.hoisted(() => vi.fn());
vi.mock("@/lib/sse", () => ({ streamChat }));

describe("useChatStream outbound events (live SSE path)", () => {
  beforeEach(() => {
    streamChat.mockReset();
    useChatStore.setState({
      sessions: [{ id: 1, title: "t", backend_session_id: 1, messages: [] }],
      activeSessionId: 1,
    });
  });

  it("dispatches the three outbound events onto the turn's assistant message", async () => {
    streamChat.mockImplementation(
      (_body: unknown, h: { onEvent: (e: string, d: unknown) => void }) => {
        h.onEvent("session", { run_id: 9, session_id: 1 });
        h.onEvent("outbound_rewritten", {
          log_id: 1, sent_text: "a", original_text: "b", reason: "r", destination: "d",
        });
        h.onEvent("outbound_held", {
          held_id: "h1", held_ids: ["h1"], text: "q", reason: "r", destination: "d",
          status: "pending",
        });
        h.onEvent("outbound_blocked", { log_id: 2, text: "x", reason: "r", term: "T" });
        return Promise.resolve();
      },
    );
    const { result } = renderHook(() => useChatStream());
    await result.current.send(1, "hello");
    const assistant = useChatStore.getState().sessions[0]!.messages[1]!;
    expect(assistant.agent_events!.map((e) => e.event)).toEqual([
      "outbound_rewritten",
      "outbound_held",
      "outbound_blocked",
    ]);
  });

  it("keeps distinct blocked/rewritten events whose log row id is unknown", async () => {
    streamChat.mockImplementation(
      (_body: unknown, h: { onEvent: (e: string, d: unknown) => void }) => {
        h.onEvent("session", { run_id: 9, session_id: 1 });
        h.onEvent("outbound_blocked", { log_id: null, text: "x", reason: "r" });
        h.onEvent("outbound_blocked", { log_id: null, text: "y", reason: "r" });
        h.onEvent("outbound_blocked", { log_id: null, text: "y", reason: "r" }); // replay
        h.onEvent("outbound_rewritten", {
          log_id: null, sent_text: "a", original_text: "b", reason: "r", destination: "d",
        });
        h.onEvent("outbound_rewritten", {
          log_id: null, sent_text: "c", original_text: "b", reason: "r", destination: "d",
        });
        return Promise.resolve();
      },
    );
    const { result } = renderHook(() => useChatStream());
    await result.current.send(1, "hello");
    const assistant = useChatStore.getState().sessions[0]!.messages[1]!;
    expect(assistant.agent_events!.map((e) => e.event)).toEqual([
      "outbound_blocked",
      "outbound_blocked",
      "outbound_rewritten",
      "outbound_rewritten",
    ]);
  });

  it("an approved follow-up posts approved_held_id with an empty message", async () => {
    streamChat.mockResolvedValue(undefined);
    const { result } = renderHook(() => useChatStream());
    await result.current.send(1, "", {
      approvedHeldId: "h1",
      userBubble: "Sent search: q",
    });
    const body = streamChat.mock.calls[0]![0] as Record<string, unknown>;
    expect(body).toMatchObject({ approved_held_id: "h1", user_message: "" });
    const user = useChatStore.getState().sessions[0]!.messages[0]!;
    expect(user).toMatchObject({ content: "Sent search: q", approved_held_id: "h1" });
  });
});
