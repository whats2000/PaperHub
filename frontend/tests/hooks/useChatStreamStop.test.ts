/**
 * Tests the synchronous stop() path in useChatStream (FR-15).
 *
 * Key scenario: streamChat RESOLVES (not rejects) when aborted — the worst
 * case the brief calls out. The hook must react synchronously on stop(), not
 * wait for the promise to settle.
 */
import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock @/lib/sse so streamChat is fully controlled.
vi.mock("@/lib/sse", () => {
  return {
    streamChat: vi.fn(),
  };
});

// Mock cancelRun to avoid real HTTP; also mock other api exports to satisfy
// the module (they are tree-shaken in prod but imported at module scope here).
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    cancelRun: vi.fn().mockResolvedValue({ kept: false }),
    fetchSessionMessages: vi.fn().mockResolvedValue([]),
    listSessionReferences: vi.fn().mockResolvedValue([]),
  };
});

import { STOP_EXIT_MS, useChatStream } from "@/hooks/useChatStream";

/** The stopped turn first collapses (carry-forward B), then is removed. */
async function afterExit(): Promise<void> {
  await act(async () => {
    await new Promise((r) => setTimeout(r, STOP_EXIT_MS + 30));
  });
}
import { useChatStore } from "@/store/chat";
import { useSlidesStore } from "@/store/slides";
import { streamChat } from "@/lib/sse";
import { cancelRun, fetchSessionMessages } from "@/lib/api";

const mockStreamChat = vi.mocked(streamChat);
const mockCancelRun = vi.mocked(cancelRun);
const mockFetchMessages = vi.mocked(fetchSessionMessages);

beforeEach(() => {
  useChatStore.getState().reset();
  useSlidesStore.setState({
    open: false,
    deckBySession: {},
    currentPageBySession: {},
  });
  mockStreamChat.mockReset();
  mockCancelRun.mockReset();
  mockCancelRun.mockResolvedValue({ kept: false });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useChatStream stop()", () => {
  it("resolves-on-abort: retract both messages, restore draft, call cancelRun", async () => {
    const USER_TEXT = "what is attention?";
    const RUN_ID = 99;

    // streamChat emits session + token, then RESOLVES when the signal is aborted.
    // This is the worst-case: the SSE lib does NOT reject on abort, it just resolves.
    mockStreamChat.mockImplementation(async (_body, handlers, signal) => {
      // Emit session event so runIdRef is populated
      handlers.onEvent("session", { run_id: RUN_ID, session_id: 42 });
      // Emit a token so there is streamed content
      handlers.onEvent("token", { run_id: RUN_ID, branch: "", text: "Hello" });

      // Wait for abort then resolve (not reject)
      await new Promise<void>((resolve) => {
        if (signal?.aborted) {
          resolve();
          return;
        }
        signal?.addEventListener("abort", () => resolve());
      });
      // RESOLVE — no throw, no reject.
    });

    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    // Start streaming — don't await yet; we need to call stop() while it runs
    let sendPromise: Promise<void>;
    act(() => {
      sendPromise = result.current.send(sessionId, USER_TEXT);
    });

    // Give the mock a tick to emit session + token events
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    // Verify streaming state: two messages in flight
    const sessionBeforeStop = useChatStore.getState().sessions.find((s) => s.id === sessionId)!;
    expect(sessionBeforeStop.messages).toHaveLength(2);

    // Call stop() synchronously — this is the key assertion
    act(() => {
      result.current.stop();
    });

    // Wait for the send promise to resolve (it will since the lib resolves on abort)
    await act(async () => {
      await sendPromise!;
    });

    // The pair animates out first, then both are retracted.
    const exiting = useChatStore.getState().sessions.find((s) => s.id === sessionId)!;
    expect(exiting.messages.every((m) => m.exiting)).toBe(true);
    await afterExit();
    const sessionAfterStop = useChatStore.getState().sessions.find((s) => s.id === sessionId)!;
    expect(sessionAfterStop.messages).toHaveLength(0);

    // Composer draft restored to the original user text
    expect(useChatStore.getState().composerDraft).toBe(USER_TEXT);

    // cancelRun called with the run_id learned from the session event
    expect(mockCancelRun).toHaveBeenCalledWith(RUN_ID);
    expect(mockCancelRun).toHaveBeenCalledTimes(1);
  });

  it("reattach: stop() fires cancelRun(77) and retracts the pair when send() never ran", async () => {
    // Simulates a page refresh where the user reattaches to an in-flight run.
    // send() was NOT called this render — sessionIdRef and runIdRef are null.
    // stop() must resolve sid from activeSessionId and rid from the trailing
    // processing message's run_id.
    const RUN_ID = 77;

    const sessionId = useChatStore.getState().newSession();
    // Seed the store: activeSessionId + trailing processing assistant with run_id.
    useChatStore.setState({ activeSessionId: sessionId });
    useChatStore.getState().appendMessage(sessionId, {
      role: "user",
      content: "question after refresh",
      run_id: RUN_ID,
    });
    useChatStore.getState().appendMessage(sessionId, {
      role: "assistant",
      content: "",
      run_id: RUN_ID,
      status: "processing",
    });

    const { result } = renderHook(() => useChatStream());
    // send() is never called — refs are null.

    act(() => {
      result.current.stop();
    });

    // cancelRun must be called with the run_id from the processing message.
    expect(mockCancelRun).toHaveBeenCalledWith(RUN_ID);
    expect(mockCancelRun).toHaveBeenCalledTimes(1);

    await afterExit();
    // Both messages are removed after the exit (0 remaining).
    const session = useChatStore.getState().sessions.find((s) => s.id === sessionId)!;
    expect(session.messages).toHaveLength(0);

    // Composer restored to the user message text.
    expect(useChatStore.getState().composerDraft).toBe("question after refresh");
  });

  it("stop() before any session event: retracts pair, does NOT call cancelRun", async () => {
    const USER_TEXT = "hello";

    // streamChat that never emits any events, waits for abort then resolves
    mockStreamChat.mockImplementation(async (_body, _handlers, signal) => {
      await new Promise<void>((resolve) => {
        if (signal?.aborted) { resolve(); return; }
        signal?.addEventListener("abort", () => resolve());
      });
    });

    const sessionId = useChatStore.getState().newSession();
    const { result } = renderHook(() => useChatStream());

    let sendPromise: Promise<void>;
    act(() => {
      sendPromise = result.current.send(sessionId, USER_TEXT);
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    act(() => {
      result.current.stop();
    });

    await act(async () => {
      await sendPromise!;
    });

    await afterExit();
    const session = useChatStore.getState().sessions.find((s) => s.id === sessionId)!;
    expect(session.messages).toHaveLength(0);
    expect(useChatStore.getState().composerDraft).toBe(USER_TEXT);
    // No run_id was seen, so cancelRun must NOT be called
    expect(mockCancelRun).not.toHaveBeenCalled();
  });

  it("concern 5: a turn the backend kept becomes the stub, never animates out", async () => {
    const RUN_ID = 55;
    mockCancelRun.mockResolvedValue({ kept: true });
    mockFetchMessages.mockResolvedValue([
      { role: "user", content: "add BERT", run_id: RUN_ID, created_at: "", run_status: "cancelled" },
      {
        role: "assistant", content: "", run_id: RUN_ID, created_at: "", run_status: "cancelled",
        agent_events: [{ event: "paper_added", data: { papers_id: 1, paper_content_id: 68, title: "BERT", undoable: true } }],
      },
    ]);
    const sessionId = useChatStore.getState().newSession();
    useChatStore.setState({ activeSessionId: sessionId });
    useChatStore.getState().patchSessionBackendId(sessionId, 9);
    useChatStore.getState().appendMessage(sessionId, { role: "user", content: "add BERT", run_id: RUN_ID });
    useChatStore.getState().appendMessage(sessionId, {
      role: "assistant", content: "", run_id: RUN_ID, status: "processing",
    });
    const { result } = renderHook(() => useChatStream());
    act(() => {
      result.current.stop();
    });
    // Waiting for the backend: still in place, nothing exiting, composer untouched.
    let msgs = useChatStore.getState().sessions.find((s) => s.id === sessionId)!.messages;
    expect(msgs[1]!.stopping).toBe(true);
    expect(msgs.some((m) => m.exiting)).toBe(false);
    await afterExit();
    msgs = useChatStore.getState().sessions.find((s) => s.id === sessionId)!.messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[1]!.status).toBe("stopped");
    expect(msgs[1]!.agent_events?.[0]?.event).toBe("paper_added");
    expect(useChatStore.getState().composerDraft).toBe("");
  });

  function seedStopping(runId: number) {
    const sessionId = useChatStore.getState().newSession();
    useChatStore.setState({ activeSessionId: sessionId });
    useChatStore.getState().patchSessionBackendId(sessionId, 9);
    useChatStore.getState().appendMessage(sessionId, { role: "user", content: "add BERT", run_id: runId });
    useChatStore.getState().appendMessage(sessionId, {
      role: "assistant", content: "", run_id: runId, status: "processing",
    });
    return sessionId;
  }

  it("residual D2: a failed cancel re-reads the chat and keeps a stub the backend kept", async () => {
    const RUN_ID = 56;
    mockCancelRun.mockRejectedValue(new Error("network"));
    mockFetchMessages.mockResolvedValue([
      { role: "user", content: "add BERT", run_id: RUN_ID, created_at: "", run_status: "cancelled" },
      {
        role: "assistant", content: "", run_id: RUN_ID, created_at: "", run_status: "cancelled",
        agent_events: [{ event: "paper_added", data: { papers_id: 1, paper_content_id: 68, title: "BERT", undoable: true } }],
      },
    ]);
    const sessionId = seedStopping(RUN_ID);
    const { result } = renderHook(() => useChatStream());
    act(() => {
      result.current.stop();
    });
    await afterExit();
    const msgs = useChatStore.getState().sessions.find((s) => s.id === sessionId)!.messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[1]!.status).toBe("stopped");
    expect(msgs[1]!.agent_events?.[0]?.event).toBe("paper_added");
  });

  it("residual D2: a failed cancel whose re-read shows no stub retracts the turn", async () => {
    mockCancelRun.mockRejectedValue(new Error("network"));
    mockFetchMessages.mockResolvedValue([]);
    const sessionId = seedStopping(57);
    const { result } = renderHook(() => useChatStream());
    act(() => {
      result.current.stop();
    });
    await afterExit();
    const msgs = useChatStore.getState().sessions.find((s) => s.id === sessionId)!.messages;
    expect(msgs).toHaveLength(0);
    expect(useChatStore.getState().composerDraft).toBe("add BERT");
  });
});
