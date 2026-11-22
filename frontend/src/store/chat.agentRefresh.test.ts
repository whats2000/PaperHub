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

describe("notes_changed (H5b)", () => {
  beforeEach(() => {
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

  const ev = {
    change_set_id: "cs_1",
    note_id: 9,
    note_title: "Attention",
    summary: { added: 1, updated: 0, deleted: 0, suggested: 0, rejected: 0, section: null },
    revision: 3,
  };

  it("adds one card per change set, live or replayed by the reattach poller", async () => {
    const { useNotesStore } = await import("@/store/notes");
    const loadNote = vi.fn(() => Promise.resolve());
    useNotesStore.setState({ loadNote } as never);
    const s = useChatStore.getState();
    s.applyRunEvent(1, { event: "notes_changed", data: JSON.stringify(ev) });
    s.applyAgentEvent(1, 7, "notes_changed", ev);
    const events = useChatStore.getState().sessions[0]!.messages[0]!.agent_events ?? [];
    expect(events).toEqual([{ event: "notes_changed", data: ev }]);
    // The note is not loaded anywhere: nothing to reload.
    expect(loadNote).not.toHaveBeenCalled();
  });

  it("reloads the note in the notes store when it is loaded", async () => {
    const { useNotesStore } = await import("@/store/notes");
    const loadNote = vi.fn(() => Promise.resolve());
    useNotesStore.setState({ notes: { 9: {} as never }, loadNote } as never);
    useChatStore.getState().applyAgentEvent(1, 7, "notes_changed", ev);
    expect(loadNote).toHaveBeenCalledWith(9);
  });

  it("keeps a stopped turn that already changed a note", () => {
    const s = useChatStore.getState();
    s.applyAgentEvent(1, 7, "notes_changed", ev);
    expect(s.stopTurn(1)).toEqual({ kept: true });
  });
});

describe("notes_changed of a mark recolour (Task 16)", () => {
  beforeEach(() => {
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

  it("redraws the reader's paper of every recoloured mark", async () => {
    const { onPaperMarksChanged } = await import("@/store/notes");
    const papers: number[][] = [];
    const off = onPaperMarksChanged((ids) => papers.push([...ids]));
    useChatStore.getState().applyAgentEvent(1, 7, "notes_changed", {
      change_set_id: "cs_2",
      note_id: 9,
      note_title: "Chat",
      summary: { added: 0, updated: 1, deleted: 0, suggested: 0, rejected: 0, section: null },
      revision: 3,
      marks: [{ mark_id: "mk_1", paper_id: 4, block_id: "q1", color: { from: "green", to: "blue" }, blocks: [] }],
    });
    off();
    expect(papers).toEqual([[4]]);
  });
});
