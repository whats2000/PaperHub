import { useCallback, useEffect, useRef } from "react";

import type { ChatSession } from "@/types/domain";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { RoutingBadge } from "@/components/chat/RoutingBadge";
import { TraceInline } from "@/components/chat/TraceInline";
import { EmptyState } from "@/components/states/EmptyState";
import { useChatStore } from "@/store/chat";
import { useChatStream } from "@/hooks/useChatStream";

export function ChatThread({ session }: { session: ChatSession | null }) {
  const endRef = useRef<HTMLDivElement>(null);
  const lastMessage = session?.messages[session.messages.length - 1];
  const { send } = useChatStream();

  useEffect(() => {
    if (!endRef.current) return;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
    });
  }, [session?.messages.length, lastMessage?.content]);

  const retryFrom = useCallback(
    (errorIndex: number, userContent: string) => {
      if (!session) return;
      // Remove the failed assistant message; send will append a new one.
      useChatStore.getState().removeMessage(session.id, errorIndex);
      // Don't remove the user message — keep one user bubble, re-send.
      void send(session.id, userContent, { skipUserAppend: true });
    },
    [session, send],
  );

  if (!session || session.messages.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div
        className="max-w-3xl mx-auto p-4 space-y-4 pb-12"
        aria-live="polite"
        aria-atomic="false"
      >
        {session.messages.map((msg, i) => {
          // For error assistant messages, find the preceding user message for retry.
          let retryHandler: (() => void) | undefined;
          if (msg.role === "assistant" && msg.status === "error") {
            let userContent: string | undefined;
            for (let j = i - 1; j >= 0; j--) {
              if (session.messages[j]?.role === "user") {
                userContent = session.messages[j]!.content;
                break;
              }
            }
            if (userContent !== undefined) {
              const capturedIndex = i;
              const capturedContent = userContent;
              retryHandler = () => retryFrom(capturedIndex, capturedContent);
            }
          }

          return (
            <div key={`${msg.run_id ?? "user"}-${i}`} className="space-y-1">
              <MessageBubble message={msg} onRetry={retryHandler} />
              {msg.role === "assistant" && msg.routing_decision && (
                <div className="flex justify-start pl-1">
                  <RoutingBadge decision={msg.routing_decision} />
                </div>
              )}
              {msg.role === "assistant" && msg.trace && msg.trace.length > 0 && (
                <div className="pl-1">
                  <TraceInline trace={msg.trace} />
                </div>
              )}
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
