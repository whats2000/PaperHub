import { beforeEach, describe, expect, it, vi } from "vitest";

const { listSessionReferences, listMemories } = vi.hoisted(() => ({
  listSessionReferences: vi.fn(() => Promise.resolve([])),
  listMemories: vi.fn(() => Promise.resolve([])),
}));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listSessionReferences,
  listMemories,
}));

import { useChatStore } from "@/store/chat";

describe("agent chip events refresh the panels on the reattach path too", () => {
  beforeEach(() => {
    listSessionReferences.mockClear();
    listMemories.mockClear();
    useChatStore.getState().reset();
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "t",
          backend_session_id: 5,
          messages: [{ role: "assistant", content: "", run_id: 7, status: "processing" }],
        },
      ],
      activeSessionId: 1,
    });
  });

  it("paper_added refreshes references, memory_saved refreshes memories", () => {
    const s = useChatStore.getState();
    s.applyRunEvent(1, {
      event: "paper_added",
      data: JSON.stringify({ papers_id: 3, paper_content_id: 2, title: "T" }),
    });
    expect(listSessionReferences).toHaveBeenCalledWith(5);
    s.applyRunEvent(1, {
      event: "memory_saved",
      data: JSON.stringify({ memory_id: 4, content: "m", scope: "global" }),
    });
    expect(listMemories).toHaveBeenCalledWith(5);
  });
});
