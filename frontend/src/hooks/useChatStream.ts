import { useCallback, useRef } from "react";
import type { RoutingDecision, ToolCallRecord } from "@/types/domain";
import { streamChat } from "@/lib/sse";
import { useChatStore } from "@/store/chat";

interface ToolStepData { record: ToolCallRecord; }
interface RoutingData { run_id: number; branch: string; decision: RoutingDecision; }
interface TokenData { run_id: number; branch: string; text: string; }
interface FinalData { run_id: number; branch: string; message_id: number; content: string; }
interface ErrorData { run_id: number; branch: string; message: string; }

export function useChatStream() {
  const abortRef = useRef<AbortController | null>(null);
  const store = useChatStore;

  const send = useCallback(async (sessionId: number, userMessage: string, opts?: { skipUserAppend?: boolean }) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    // Snapshot prior turns BEFORE we append the new user + assistant placeholder.
    const priorMessages = store.getState().sessions.find((s) => s.id === sessionId)?.messages ?? [];
    const history = priorMessages
      .filter((m) => m.status !== "error" && m.status !== "streaming")
      .filter((m) => m.content.length > 0)
      .map((m) => ({ role: m.role, content: m.content }));

    if (!opts?.skipUserAppend) {
      store.getState().appendMessage(sessionId, {
        role: "user", content: userMessage, run_id: null,
      });
    }
    store.getState().appendMessage(sessionId, {
      role: "assistant", content: "", run_id: null, status: "streaming",
    });
    let runId: number | null = null;
    // True once the error has been rendered inline (mid-stream case). The outer
    // catch checks this to decide whether to re-throw to ChatPage's toast.
    let handledInline = false;

    try {
      await streamChat(
        { session_id: null, user_message: userMessage, history },
        {
          onEvent: (event, data) => {
            if (event === "tool_step") {
              const rec = (data as ToolStepData).record;
              if (runId === null) {
                runId = rec.run_id;
                store.getState().patchAssistantRunId(sessionId, runId);
              }
              store.getState().appendTrace(sessionId, rec.run_id, rec);
            } else if (event === "routing_decision") {
              const d = data as RoutingData;
              if (runId === null) {
                runId = d.run_id;
                store.getState().patchAssistantRunId(sessionId, runId);
              }
              store.getState().setRouting(sessionId, d.run_id, d.decision);
            } else if (event === "token") {
              const t = data as TokenData;
              store.getState().appendToken(sessionId, t.run_id, t.text);
            } else if (event === "final") {
              const f = data as FinalData;
              store.getState().finaliseMessage(sessionId, f.run_id, f.content);
            } else if (event === "error") {
              const e = data as ErrorData;
              store.getState().errorMessage(sessionId, e.run_id, e.message);
            }
          },
          onError: (err) => {
            const msg = err instanceof Error ? err.message : String(err);
            if (runId !== null) {
              // Mid-stream: bubble has context, inline error is enough.
              store.getState().errorMessage(sessionId, runId, msg);
              handledInline = true;
            } else {
              // Pre-event: placeholder bubble is empty, need both surfaces.
              store.getState().failPendingAssistant(sessionId, msg);
              // Don't set handledInline — outer catch re-throws → ChatPage toasts.
            }
          },
        },
        abortRef.current.signal,
      );
    } catch (err) {
      // fetchEventSource may throw synchronously before onerror fires
      // (e.g. CORS preflight reject, immediate connection refused). In that
      // case onError didn't run; runId is still null; treat as pre-event.
      if (!handledInline && runId === null) {
        const msg = err instanceof Error ? err.message : String(err);
        store.getState().failPendingAssistant(sessionId, msg);
      }
      // Only re-throw for pre-event failures so ChatPage's toast fires.
      // Mid-stream failures stay inline-only.
      if (!handledInline) {
        throw err;
      }
    }
  }, [store]);

  return { send };
}
