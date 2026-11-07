import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFeedbackBadge } from "@/hooks/useFeedbackBadge";
import { getFeedbackQueue } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import type { QueueItem } from "@/types/feedback";

vi.mock("@/lib/api", () => ({ getFeedbackQueue: vi.fn() }));
const getQueue = vi.mocked(getFeedbackQueue);

const items = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ feedback_id: i + 1 }) as QueueItem);

let hidden = false;
Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
const setHidden = (v: boolean) => {
  hidden = v;
  document.dispatchEvent(new Event("visibilitychange"));
};

beforeEach(() => {
  vi.useFakeTimers();
  hidden = false;
  getQueue.mockReset();
  useNavStore.setState({ feedbackBadge: 0 });
  useChatStore.setState({ sessions: [], activeSessionId: null });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("useFeedbackBadge", () => {
  it("sets the badge to the queue length on mount", async () => {
    getQueue.mockResolvedValue(items(3));
    renderHook(() => useFeedbackBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(useNavStore.getState().feedbackBadge).toBe(3);
  });

  it("polls every 60 s while visible and stops while the tab is hidden", async () => {
    getQueue.mockResolvedValue(items(1));
    renderHook(() => useFeedbackBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(getQueue).toHaveBeenCalledTimes(1);

    getQueue.mockResolvedValue(items(2));
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(getQueue).toHaveBeenCalledTimes(2);
    expect(useNavStore.getState().feedbackBadge).toBe(2);

    act(() => setHidden(true));
    await act(() => vi.advanceTimersByTimeAsync(180_000));
    expect(getQueue).toHaveBeenCalledTimes(2);

    // Visible again: refresh at once, then resume the interval.
    act(() => setHidden(false));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(getQueue).toHaveBeenCalledTimes(3);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(getQueue).toHaveBeenCalledTimes(4);
  });

  it("refreshes after a chat turn finishes", async () => {
    getQueue.mockResolvedValue(items(0));
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "s",
          backend_session_id: 7,
          messages: [
            { role: "user", content: "q" },
            { role: "assistant", content: "", status: "streaming", run_id: 42 },
          ],
        },
      ],
    } as never);
    renderHook(() => useFeedbackBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(getQueue).toHaveBeenCalledTimes(1);

    // A token while streaming is not a finished turn.
    act(() => useChatStore.getState().appendToken(1, 42, "a"));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(getQueue).toHaveBeenCalledTimes(1);

    getQueue.mockResolvedValue(items(1));
    act(() => useChatStore.getState().finaliseMessage(1, 42, "answer"));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(getQueue).toHaveBeenCalledTimes(2);
    expect(useNavStore.getState().feedbackBadge).toBe(1);

    // One delayed follow-up for the item the background review queues later.
    getQueue.mockResolvedValue(items(2));
    await act(() => vi.advanceTimersByTimeAsync(12_000));
    expect(getQueue).toHaveBeenCalledTimes(3);
    expect(useNavStore.getState().feedbackBadge).toBe(2);
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(getQueue).toHaveBeenCalledTimes(3);
  });

  it("cancels the follow-up refresh on unmount", async () => {
    getQueue.mockResolvedValue(items(0));
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "s",
          backend_session_id: 7,
          messages: [{ role: "assistant", content: "", status: "streaming", run_id: 9 }],
        },
      ],
    } as never);
    const { unmount } = renderHook(() => useFeedbackBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    act(() => useChatStore.getState().finaliseMessage(1, 9, "done"));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(getQueue).toHaveBeenCalledTimes(2);
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(12_000));
    expect(getQueue).toHaveBeenCalledTimes(2);
  });

  it("keeps the last badge when the queue request fails", async () => {
    useNavStore.setState({ feedbackBadge: 4 });
    getQueue.mockRejectedValue(new Error("down"));
    renderHook(() => useFeedbackBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(useNavStore.getState().feedbackBadge).toBe(4);
  });
});
