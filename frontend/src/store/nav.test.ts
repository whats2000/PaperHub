import { afterEach, describe, expect, it } from "vitest";

import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";

afterEach(() => {
  useNavStore.setState({ page: "chat", feedbackBadge: 0 });
  useChatStore.getState().reset();
});

describe("nav store", () => {
  it("defaults to chat and switches pages", () => {
    expect(useNavStore.getState().page).toBe("chat");
    useNavStore.getState().setPage("ideas");
    expect(useNavStore.getState().page).toBe("ideas");
    useNavStore.getState().setPage("feedback");
    expect(useNavStore.getState().page).toBe("feedback");
    useNavStore.getState().setFeedbackBadge(4);
    expect(useNavStore.getState().feedbackBadge).toBe(4);
  });

  it("selecting a chat session returns to the chat page", () => {
    const id = useChatStore.getState().newSession();
    useNavStore.getState().setPage("ideas");
    useChatStore.getState().selectSession(id);
    expect(useNavStore.getState().page).toBe("chat");
  });

  it("starting a new chat returns to the chat page", () => {
    useNavStore.getState().setPage("feedback");
    useChatStore.getState().newSession();
    expect(useNavStore.getState().page).toBe("chat");
  });
});

describe("setPausedList (H5a)", () => {
  it("counts a paused run's actions and Ask-first search cards per chat", () => {
    useNavStore.getState().setPausedList([
      { session_id: 1, kind: "action" },
      { session_id: 1, kind: "search" },
      { session_id: 2, kind: "search" },
      // Held rows are counted from the held list, not here.
      { session_id: 2, kind: "held" },
    ]);
    expect(useNavStore.getState().pausedActionsBySession).toEqual({ 1: 2, 2: 1 });
  });
});
