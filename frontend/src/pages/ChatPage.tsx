import { toast } from "sonner";

import { ChatThread } from "@/components/chat/ChatThread";
import { Composer } from "@/components/chat/Composer";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useGlobalShortcuts } from "@/hooks/useGlobalShortcuts";
import { useReferencesSync } from "@/hooks/useReferencesSync";

export function ChatPage() {
  useGlobalShortcuts();
  useReferencesSync();
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const newSession = useChatStore((s) => s.newSession);
  const { send } = useChatStream();

  const activeSession =
    activeSessionId === null
      ? null
      : (sessions.find((s) => s.id === activeSessionId) ?? null);

  const isStreaming =
    activeSession?.messages.some((m) => m.status === "streaming") ?? false;

  const handleSubmit = (text: string): void => {
    const sessionId = activeSessionId ?? newSession();
    send(sessionId, text).catch((err: unknown) => {
      toast.error("Request failed", {
        description: err instanceof Error ? err.message : String(err),
      });
    });
  };

  return (
    <div className="flex flex-1 flex-col min-h-0">
      <ChatThread session={activeSession} />
      <Composer onSubmit={handleSubmit} disabled={isStreaming} />
    </div>
  );
}
