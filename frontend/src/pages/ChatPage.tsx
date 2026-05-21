import { lazy, Suspense } from "react";
import { toast } from "sonner";

import { ChatThread } from "@/components/chat/ChatThread";
import { Composer } from "@/components/chat/Composer";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { useGlobalShortcuts } from "@/hooks/useGlobalShortcuts";
import { useReferencesSync } from "@/hooks/useReferencesSync";
import { cn } from "@/lib/utils";

const CitationCanvas = lazy(() =>
  import("@/components/canvas/CitationCanvas").then((m) => ({
    default: m.CitationCanvas,
  })),
);

export function ChatPage() {
  useGlobalShortcuts();
  useReferencesSync();
  const canvasOpen = useCanvasStore((s) => s.open);
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
    <div
      className={cn(
        "grid flex-1 min-h-0 transition-[grid-template-columns] duration-200",
        canvasOpen
          ? "grid-cols-[1fr_clamp(360px,38vw,560px)]"
          : "grid-cols-[1fr_0px]",
      )}
    >
      <div className="flex min-h-0 min-w-0 flex-col">
        <ChatThread session={activeSession} />
        <Composer onSubmit={handleSubmit} disabled={isStreaming} />
      </div>
      <div className="min-h-0 overflow-hidden">
        {canvasOpen && (
          <Suspense fallback={null}>
            <CitationCanvas />
          </Suspense>
        )}
      </div>
    </div>
  );
}
