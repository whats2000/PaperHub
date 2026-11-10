import { beforeEach, describe, expect, it } from "vitest";

import { useChatStore } from "@/store/chat";
import type { AgentUiEvent, BackendMessage, ChatMessage } from "@/types/domain";

function seed(assistant: Partial<ChatMessage>) {
  useChatStore.getState().reset();
  useChatStore.setState({
    sessions: [
      {
        id: 1,
        title: "t",
        backend_session_id: 5,
        messages: [
          { role: "user", content: "earlier", run_id: 6, status: "ok" },
          { role: "assistant", content: "answer", run_id: 6, status: "ok" },
          { role: "user", content: "add X and read it", run_id: null },
          { role: "assistant", content: "partial", run_id: 7, status: "streaming", ...assistant },
        ],
      },
    ],
    activeSessionId: 1,
  });
}

const msgs = () => useChatStore.getState().sessions[0]!.messages;

describe("Stop (review #5, carry-forward B)", () => {
  beforeEach(() => seed({}));

  it("keeps a turn without local chips in place until the backend answers, then retracts it", () => {
    const res = useChatStore.getState().stopTurn(1);
    expect(res).toEqual({ kept: false });
    expect(msgs()[3]!.stopping).toBe(true);
    expect(msgs().some((m) => m.exiting)).toBe(false);
    // Backend: retracted (kept=false) -> animate out, text back to composer.
    expect(useChatStore.getState().retractStopping(1)).toBe("add X and read it");
    // Still rendered while it animates out.
    expect(msgs().slice(2).map((m) => m.exiting)).toEqual([true, true]);
    // A new turn sent during the exit must survive the removal.
    useChatStore.getState().appendMessage(1, { role: "user", content: "next", run_id: null });
    useChatStore.getState().removeExiting(1);
    expect(msgs().map((m) => m.content)).toEqual(["earlier", "answer", "next"]);
  });

  it("keeps a turn whose actions already ran, as a stopped stub with its chips", () => {
    const events: AgentUiEvent[] = [
      { event: "paper_added", data: { papers_id: 3, paper_content_id: 2, title: "X", undoable: true } },
      {
        event: "confirm_request",
        data: { action_id: "a", tool: "remove_paper", args_summary: "s", reason: "", status: "pending" },
      },
    ];
    seed({ agent_events: events });
    const res = useChatStore.getState().stopTurn(1);
    expect(res.kept).toBe(true);
    const last = msgs()[3]!;
    expect(last.status).toBe("stopped");
    expect(last.content).toBe("");
    expect(last.exiting).toBeUndefined();
    expect(last.agent_events?.[0]?.event).toBe("paper_added");
    // The backend dismisses the stopped run's confirmations.
    expect((last.agent_events?.[1]?.data as { status: string }).status).toBe("dismissed");
    expect(msgs()[2]!.content).toBe("add X and read it");
  });

  it("turns a stopping turn the backend kept into the stub with the run's chips", () => {
    useChatStore.getState().stopTurn(1);
    useChatStore.getState().keepStopping(1, [
      { event: "memory_saved", data: { memory_id: 4, content: "m", scope: "global", undoable: true } },
    ]);
    const last = msgs()[3]!;
    expect(last.status).toBe("stopped");
    expect(last.stopping).toBe(false);
    expect(last.content).toBe("");
    expect(last.agent_events?.[0]?.event).toBe("memory_saved");
    expect(msgs()[2]!.exiting).toBeUndefined();
  });

  it("replays a cancelled run's stub as stopped", () => {
    seed({ status: "ok" });
    const backend: BackendMessage[] = [
      { role: "user", content: "q", run_id: 9, created_at: "", run_status: "cancelled" },
      { role: "assistant", content: "", run_id: 9, created_at: "", run_status: "cancelled" },
    ];
    useChatStore.getState().hydrateSessionMessages(1, backend);
    expect(msgs()[1]!.status).toBe("stopped");
  });
});
