import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatThread } from "@/components/chat/ChatThread";
import type { ChatSession, ChatMessage } from "@/types/domain";

// jsdom doesn't implement scrollIntoView; capture the options it's called with.
let scrollSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  scrollSpy = vi.fn();
  Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function session(messages: ChatMessage[]): ChatSession {
  return { id: 1, title: "t", messages, backend_session_id: 10 };
}

const userMsg: ChatMessage = { role: "user", content: "hi", run_id: null };

describe("ChatThread auto-scroll during streaming", () => {
  it("uses smooth scroll when a NEW message arrives", () => {
    const { rerender } = render(<ChatThread session={session([userMsg])} />);
    scrollSpy.mockClear();

    // A new assistant message appears (length changes).
    rerender(
      <ChatThread
        session={session([
          userMsg,
          { role: "assistant", content: "", run_id: 1, status: "streaming" },
        ])}
      />,
    );

    expect(scrollSpy).toHaveBeenCalled();
    expect(scrollSpy.mock.lastCall?.[0]).toMatchObject({ behavior: "smooth" });
  });

  it("uses instant ('auto') scroll while a streaming message's content grows", () => {
    const streaming: ChatMessage = {
      role: "assistant",
      content: "Hello",
      run_id: 1,
      status: "streaming",
    };
    const { rerender } = render(<ChatThread session={session([userMsg, streaming])} />);
    scrollSpy.mockClear();

    // Same message count — only the last message's content grew (a token).
    rerender(
      <ChatThread
        session={session([userMsg, { ...streaming, content: "Hello world" }])}
      />,
    );

    expect(scrollSpy).toHaveBeenCalled();
    // The fix: token growth must NOT launch a fresh smooth animation (which
    // oscillates). It should jump instantly to keep the view pinned to bottom.
    expect(scrollSpy.mock.lastCall?.[0]).toMatchObject({ behavior: "auto" });
  });
});
