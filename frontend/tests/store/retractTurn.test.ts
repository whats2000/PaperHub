import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "@/store/chat";

beforeEach(() => {
  useChatStore.getState().reset();
});

describe("retractTurn", () => {
  it("removes both the trailing assistant and user messages and returns the user text", () => {
    const sid = useChatStore.getState().newSession();
    useChatStore.getState().appendMessage(sid, { role: "user", content: "hi", run_id: null });
    useChatStore.getState().appendMessage(sid, {
      role: "assistant", content: "", run_id: null, status: "streaming",
    });

    const restored = useChatStore.getState().retractTurn(sid);

    expect(restored).toBe("hi");
    const session = useChatStore.getState().sessions.find((s) => s.id === sid)!;
    expect(session.messages).toHaveLength(0);
  });

  it("returns empty string when there is no user message to retract", () => {
    const sid = useChatStore.getState().newSession();
    // Only an assistant placeholder (edge-case: no paired user msg)
    useChatStore.getState().appendMessage(sid, {
      role: "assistant", content: "", run_id: null, status: "streaming",
    });

    const restored = useChatStore.getState().retractTurn(sid);

    expect(restored).toBe("");
    const session = useChatStore.getState().sessions.find((s) => s.id === sid)!;
    expect(session.messages).toHaveLength(0);
  });

  it("is a no-op for an unknown session", () => {
    const restored = useChatStore.getState().retractTurn(9999);
    expect(restored).toBe("");
  });
});
