import { lazy, Suspense } from "react";
import { toast } from "sonner";

import { ChatThread } from "@/components/chat/ChatThread";
import { Composer } from "@/components/chat/Composer";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { useGlobalShortcuts } from "@/hooks/useGlobalShortcuts";
import { useReferencesSync } from "@/hooks/useReferencesSync";
import { useSessionsSync } from "@/hooks/useSessionsSync";
import { useCloseCanvasOnSessionChange } from "@/hooks/useCloseCanvasOnSessionChange";
import { useCanvasResize } from "@/hooks/useCanvasResize";
import { cn } from "@/lib/utils";

const CitationCanvas = lazy(() =>
  import("@/components/canvas/CitationCanvas").then((m) => ({
    default: m.CitationCanvas,
  })),
);

export function ChatPage() {
  useGlobalShortcuts();
  useSessionsSync();
  useReferencesSync();
  const canvasOpen = useCanvasStore((s) => s.open);
  const { width: canvasWidth, resizing, onPointerDown } = useCanvasResize();
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const newSession = useChatStore((s) => s.newSession);
  const { send } = useChatStream();

  // Close the canvas when the user switches chat sessions (it shows the
  // previous session's references).
  useCloseCanvasOnSessionChange(activeSessionId);

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
        "grid flex-1 min-h-0",
        // No width transition while dragging the divider, so it tracks the cursor.
        !resizing && "transition-[grid-template-columns] duration-200",
      )}
      style={{ gridTemplateColumns: `1fr ${canvasOpen ? canvasWidth : 0}px` }}
    >
      <div className="flex min-h-0 min-w-0 flex-col">
        <ChatThread session={activeSession} />
        <Composer onSubmit={handleSubmit} disabled={isStreaming} />
      </div>
      {/* Canvas stays mounted for the whole session (collapsed to 0 width when
          closed) so its prefetched, kept-alive paper iframes survive open/close
          and don't re-render on re-open. */}
      <div className="relative min-h-0 overflow-hidden">
        {canvasOpen && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize reference panel"
            onPointerDown={onPointerDown}
            className="absolute left-0 top-0 z-10 h-full w-1.5 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
          />
        )}
        {/* Disable iframe pointer events while dragging so the cursor can't
            enter the cross-document iframe and swallow the window pointermove. */}
        <div className={cn("h-full", resizing && "pointer-events-none")}>
          <Suspense fallback={null}>
            <CitationCanvas />
          </Suspense>
        </div>
      </div>
    </div>
  );
}
