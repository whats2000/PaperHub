import { http, HttpResponse } from "msw";

import { API_BASE_URL } from "@/lib/api";

const enc = new TextEncoder();

function sseChunk(event: string, data: unknown): Uint8Array {
  return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

export const chitchatHappyPath = http.post(`${API_BASE_URL}/chat`, () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(
        sseChunk("session", { run_id: 1, session_id: 10 }),
      );
      controller.enqueue(
        sseChunk("tool_step", {
          record: {
            run_id: 1, branch: "", step_index: 0, agent: "router", tool: "classify",
            model: "x", latency_ms: 12, status: "ok",
            parent_step: null, args_redacted_json: null, result_summary_json: null,
            token_in: null, token_out: null, error: null,
          },
        }),
      );
      controller.enqueue(
        sseChunk("routing_decision", {
          run_id: 1, branch: "",
          decision: {
            intent: "chitchat", model_tier: "small",
            confidence: 0.9, reasoning: "greeting",
          },
        }),
      );
      controller.enqueue(sseChunk("token", { run_id: 1, branch: "", text: "Hi " }));
      controller.enqueue(sseChunk("token", { run_id: 1, branch: "", text: "there!" }));
      controller.enqueue(
        sseChunk("final", {
          run_id: 1, branch: "", message_id: 2, content: "Hi there!",
        }),
      );
      controller.close();
    },
  });
  return new HttpResponse(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
});
