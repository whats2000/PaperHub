import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE_URL } from "@/lib/api";

export interface SseHandlers {
  onEvent: (event: string, data: unknown) => void;
  onError?: (err: unknown) => void;
  onClose?: () => void;
}

export interface ChatRequestBody {
  session_id: number | null;
  user_message: string;
  history: { role: "user" | "assistant"; content: string }[];
}

export async function streamChat(
  body: ChatRequestBody,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  await fetchEventSource(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
    openWhenHidden: true,
    onopen(response) {
      if (!response.ok) {
        throw new Error(`POST /chat failed: ${response.status} ${response.statusText}`);
      }
      return Promise.resolve();
    },
    onmessage(msg) {
      if (msg.event) {
        try {
          handlers.onEvent(msg.event, JSON.parse(msg.data) as unknown);
        } catch (e) {
          handlers.onError?.(e);
        }
      }
    },
    onerror(err) {
      handlers.onError?.(err);
      throw err;
    },
    onclose() {
      handlers.onClose?.();
    },
  });
}
