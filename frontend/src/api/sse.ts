/**
 * SSE streaming client for POST /chat.
 *
 * Uses fetch + ReadableStream rather than EventSource because EventSource
 * only supports GET requests — POST requires fetch with a streaming body reader.
 *
 * SSE wire format:
 *   data: <json>\n\n
 *
 * Each non-empty line starting with "data:" is parsed as a JSON SseEvent
 * and delivered to onEvent.
 */

import type { SseEvent } from "./types";

/**
 * Open a streaming POST to /chat, parse SSE frames, and invoke onEvent
 * for each parsed SseEvent.
 *
 * @param message  The user's message text.
 * @param onEvent  Callback invoked for each parsed SSE event.
 * @param sessionId  Optional existing session UUID (null for new session).
 * @returns  A function that aborts the stream when called.
 */
export function streamChat(
  message: string,
  onEvent: (event: SseEvent) => void,
  sessionId: string | null = null,
): () => void {
  const controller = new AbortController();

  (async () => {
    let response: Response;
    try {
      response = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal: controller.signal,
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      onEvent({ type: "error", message: String(err) });
      return;
    }

    if (!response.ok || !response.body) {
      onEvent({ type: "error", message: `HTTP ${response.status}` });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by blank lines (\n\n).
        // We split on newlines and parse each "data:" line.
        const lines = buffer.split("\n");
        // Keep the last (possibly incomplete) line in the buffer.
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const payload = trimmed.slice("data:".length).trim();
          if (!payload) continue;
          try {
            const parsed = JSON.parse(payload) as SseEvent;
            onEvent(parsed);
          } catch {
            // Malformed JSON frame — skip silently
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onEvent({ type: "error", message: String(err) });
      }
    } finally {
      reader.releaseLock();
    }
  })();

  return () => controller.abort();
}
