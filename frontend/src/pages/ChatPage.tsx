import { lazy, Suspense, useState } from "react";
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

const MemoryManager = lazy(() =>
  import("@/components/chat/MemoryManager").then((m) => ({
    default: m.MemoryManager,
  })),
);

export function ChatPage() {
  useGlobalShortcuts();
  useSessionsSync();
  useReferencesSync();
  const canvasOpen = useCanvasStore((s) => s.open);
  const toggleCanvas = useCanvasStore((s) => s.toggleCanvas);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const { width: canvasWidth, resizing, onPointerDown } = useCanvasResize();
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const newSession = useChatStore((s) => s.newSession);
  const { send } = useChatStream();

  const [memoryOpen, setMemoryOpen] = useState(false);

  // Close the canvas when the user switches chat sessions (it shows the
  // previous session's references).
  useCloseCanvasOnSessionChange(activeSessionId);

  const activeSession =
    activeSessionId === null
      ? null
      : (sessions.find((s) => s.id === activeSessionId) ?? null);

  const backendSessionId = activeSession?.backend_session_id ?? null;

  const isStreaming =
    activeSession?.messages.some((m) => m.status === "streaming") ?? false;

  // The right column is shared between the Citation Canvas and the Memory
  // Manager. Opening one closes the other; the column width + slide animation
  // is the same for both.
  const rightPanelOpen = canvasOpen || memoryOpen;

  const handleSubmit = (text: string): void => {
    const sessionId = activeSessionId ?? newSession();
    send(sessionId, text).catch((err: unknown) => {
      toast.error("Request failed", {
        description: err instanceof Error ? err.message : String(err),
      });
    });
  };

  const handleToggleMemory = (): void => {
    // Only open when there is a backend session; always allow close.
    if (!memoryOpen && backendSessionId === null) return;
    if (!memoryOpen) {
      // Opening Memory → close Canvas if it was open.
      closeCanvas();
      setMemoryOpen(true);
    } else {
      setMemoryOpen(false);
    }
  };

  const handleToggleCanvas = (): void => {
    if (!canvasOpen) {
      // Opening Canvas → close Memory if it was open.
      setMemoryOpen(false);
    }
    toggleCanvas();
  };

  return (
    <div
      className={cn(
        "grid flex-1 min-h-0",
        // No width transition while dragging the divider, so it tracks the cursor.
        !resizing && "transition-[grid-template-columns] duration-200",
      )}
      style={{ gridTemplateColumns: `1fr ${rightPanelOpen ? canvasWidth : 0}px` }}
    >
      <div className="flex min-h-0 min-w-0 flex-col">
        <ChatThread session={activeSession} />
        <Composer
          onSubmit={handleSubmit}
          disabled={isStreaming}
          memoryOpen={memoryOpen}
          onToggleMemory={handleToggleMemory}
          onToggleCanvas={handleToggleCanvas}
          canvasOpen={canvasOpen}
          memoryDisabled={backendSessionId === null}
        />
      </div>
      {/* Right panel — shared slot for Citation Canvas and Memory Manager.
          Stays mounted (collapsed to 0 width when closed) so kept-alive paper
          iframes survive open/close cycles without re-rendering. */}
      <div className="relative min-h-0 overflow-hidden">
        {rightPanelOpen && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize reference panel"
            onPointerDown={onPointerDown}
            className="absolute left-0 top-0 z-10 h-full w-1.5 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
          />
        )}
        {/* Disable pointer events while dragging so the cursor can't enter
            cross-document iframes and swallow window pointermove events. */}
        <div className={cn("h-full", resizing && "pointer-events-none")}>
          {memoryOpen && backendSessionId !== null ? (
            /* Memory Manager in the right-panel slot — same width + animation
               as the Citation Canvas. */
            <div className="flex flex-col h-full bg-card border-l border-border overflow-hidden">
              <div className="shrink-0 px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-border">
                Memory
              </div>
              <div className="flex-1 overflow-y-auto">
                <Suspense fallback={null}>
                  <MemoryManager sessionId={backendSessionId} />
                </Suspense>
              </div>
            </div>
          ) : (
            <Suspense fallback={null}>
              <CitationCanvas />
            </Suspense>
          )}
        </div>
      </div>
    </div>
  );
}
