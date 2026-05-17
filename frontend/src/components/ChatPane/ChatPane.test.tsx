/**
 * Tests for the ChatPane component with mocked streamChat.
 *
 * Verifies:
 * - RoutingBadge renders the intent label BEFORE token text appears.
 * - Token events accumulate into the assistant message.
 * - Final event resolves the loading state.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPane } from "./ChatPane";
import * as sseModule from "../../api/sse";
import type { SseEvent } from "../../api/types";
import { useChatStore } from "../../store/chat";

// --- helpers ---

type EventCallback = (event: SseEvent) => void;

function makeMockStreamChat(events: SseEvent[]) {
  return vi.fn((_message: string, onEvent: EventCallback) => {
    // Emit events asynchronously to allow React to render intermediate states
    let i = 0;
    function emitNext() {
      if (i < events.length) {
        act(() => {
          onEvent(events[i++]);
        });
        if (i < events.length) {
          setTimeout(emitNext, 0);
        }
      }
    }
    setTimeout(emitNext, 0);
    return () => {};
  });
}

// --- tests ---

describe("ChatPane", () => {
  beforeEach(() => {
    // Reset zustand store before each test
    useChatStore.setState({
      messages: [],
      routingDecision: null,
      traceSteps: [],
      isLoading: false,
      sessionId: null,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders welcome message when no messages", () => {
    render(<ChatPane />);
    expect(screen.getByText(/Welcome to PaperHub/i)).toBeInTheDocument();
  });

  it("shows RoutingBadge with intent before token text", async () => {
    const events: SseEvent[] = [
      {
        type: "routing_decision",
        data: {
          intent: "paper_qa",
          confidence: 0.95,
          model_tier: "small",
          reasoning: "paper question",
          fallback_to_user: false,
        },
      },
      {
        type: "tool_step",
        data: {
          run_id: "run-1",
          step_index: 0,
          parent_step: null,
          agent: "research_agent",
          tool: "research_qa",
          model: "claude-sonnet-4-6",
          args_redacted: { question: "What is X?" },
          result_summary: null,
          latency_ms: 100,
          token_in: null,
          token_out: null,
          status: "ok",
          error: null,
        },
      },
      {
        type: "token",
        data: "X is a novel architecture.",
      },
      {
        type: "final",
        run_id: "run-1",
        answer: "X is a novel architecture.",
      },
    ];

    const mockStreamChat = makeMockStreamChat(events);
    vi.spyOn(sseModule, "streamChat").mockImplementation(mockStreamChat);

    render(<ChatPane />);

    // Type and submit a message
    const input = screen.getByRole("textbox", { name: /message input/i });
    await userEvent.type(input, "What is X?");
    await userEvent.click(screen.getByRole("button", { name: /send message/i }));

    // Wait for routing_decision to appear
    await waitFor(() => {
      expect(screen.getByText("Paper Q&A")).toBeInTheDocument();
    });

    // Wait for token text to accumulate
    await waitFor(() => {
      expect(screen.getByText("X is a novel architecture.")).toBeInTheDocument();
    });

    // After final event, loading should be gone
    await waitFor(() => {
      expect(useChatStore.getState().isLoading).toBe(false);
    });
  });

  it("resolves loading state after final event", async () => {
    const events: SseEvent[] = [
      {
        type: "routing_decision",
        data: {
          intent: "paper_qa",
          confidence: 0.9,
          model_tier: "small",
          reasoning: "test",
          fallback_to_user: false,
        },
      },
      { type: "token", data: "The answer is 42." },
      { type: "final", run_id: "run-2", answer: "The answer is 42." },
    ];

    const mockStreamChat = makeMockStreamChat(events);
    vi.spyOn(sseModule, "streamChat").mockImplementation(mockStreamChat);

    render(<ChatPane />);

    const input = screen.getByRole("textbox", { name: /message input/i });
    await userEvent.type(input, "What is 6 times 7?");
    await userEvent.click(screen.getByRole("button", { name: /send message/i }));

    // Should be loading initially
    expect(useChatStore.getState().isLoading).toBe(true);

    // Wait for final event to clear loading
    await waitFor(() => {
      expect(useChatStore.getState().isLoading).toBe(false);
    });
  });

  it("shows chitchat reply without tool_step", async () => {
    const chitchatReply =
      "I can only answer questions about papers you have indexed in PaperHub.";
    const events: SseEvent[] = [
      {
        type: "routing_decision",
        data: {
          intent: "chitchat",
          confidence: 0.99,
          model_tier: "small",
          reasoning: "small talk",
          fallback_to_user: false,
        },
      },
      { type: "final", run_id: "run-3", answer: chitchatReply },
    ];

    const mockStreamChat = makeMockStreamChat(events);
    vi.spyOn(sseModule, "streamChat").mockImplementation(mockStreamChat);

    render(<ChatPane />);

    const input = screen.getByRole("textbox", { name: /message input/i });
    await userEvent.type(input, "Hello!");
    await userEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getByText("Off-topic")).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(useChatStore.getState().traceSteps).toHaveLength(0);
    });
  });
});
