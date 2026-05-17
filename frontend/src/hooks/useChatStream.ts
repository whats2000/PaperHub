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

  const send = useCallback(async (sessionId: number, userMessage: string) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    store.getState().appendMessage(sessionId, {
      role: "user", content: userMessage, run_id: null,
    });
    store.getState().appendMessage(sessionId, {
      role: "assistant", content: "", run_id: null, status: "streaming",
    });
    let runId: number | null = null;

    await streamChat(
      { session_id: null, user_message: userMessage },
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
          if (runId !== null) {
            store.getState().errorMessage(
              sessionId, runId, err instanceof Error ? err.message : String(err),
            );
          }
        },
      },
      abortRef.current.signal,
    );
  }, [store]);

  return { send };
}
