/**
 * Zustand store for the chat UI.
 *
 * Messages flow:
 *   1. User sends message → optimistically append user Message.
 *   2. streamChat opens SSE → routing_decision sets routingDecision.
 *   3. tool_step events accumulate in traceSteps.
 *   4. token events accumulate into a pending assistant message.
 *   5. final event closes the stream; loading → false.
 *   6. error event appends an error Message and clears loading.
 */

import { create } from "zustand";
import { streamChat } from "../api/sse";
import type { RoutingDecision, ToolCall } from "../api/types";

export interface Message {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
}

interface ChatState {
  messages: Message[];
  routingDecision: RoutingDecision | null;
  traceSteps: ToolCall[];
  isLoading: boolean;
  sessionId: string | null;

  sendMessage: (text: string) => void;
  reset: () => void;
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  routingDecision: null,
  traceSteps: [],
  isLoading: false,
  sessionId: null,

  sendMessage(text: string) {
    if (!text.trim() || get().isLoading) return;

    const userMsg: Message = { id: makeId(), role: "user", content: text };
    set((s) => ({
      messages: [...s.messages, userMsg],
      routingDecision: null,
      traceSteps: [],
      isLoading: true,
    }));

    // Accumulate token events into a pending assistant message
    let assistantContent = "";
    const assistantId = makeId();

    // Optimistically add an empty assistant message so the UI shows a spinner
    set((s) => ({
      messages: [
        ...s.messages,
        { id: assistantId, role: "assistant", content: "" } satisfies Message,
      ],
    }));

    streamChat(text, (event) => {
      switch (event.type) {
        case "routing_decision":
          set({ routingDecision: event.data });
          break;

        case "tool_step":
          set((s) => ({ traceSteps: [...s.traceSteps, event.data] }));
          break;

        case "token":
          assistantContent += event.data;
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId ? { ...m, content: assistantContent } : m,
            ),
          }));
          break;

        case "citation":
          // Phase A: citations are tracked but not rendered separately
          break;

        case "final":
          // Ensure the final answer is shown (token may have already set it)
          if (event.answer && !assistantContent) {
            assistantContent = event.answer;
          }
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId ? { ...m, content: assistantContent || event.answer } : m,
            ),
            isLoading: false,
          }));
          break;

        case "error":
          set((s) => ({
            messages: s.messages
              .filter((m) => m.id !== assistantId || assistantContent)
              .concat({ id: makeId(), role: "error", content: event.message }),
            isLoading: false,
          }));
          break;
      }
    }, get().sessionId);
  },

  reset() {
    set({
      messages: [],
      routingDecision: null,
      traceSteps: [],
      isLoading: false,
      sessionId: null,
    });
  },
}));
