import { useCallback, useEffect, useRef } from "react";

import type { ChatSession } from "@/types/domain";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { RoutingBadge } from "@/components/chat/RoutingBadge";
import { ResearchProgressCard } from "@/components/chat/ResearchProgressCard";
import { TraceInline } from "@/components/chat/TraceInline";
import { EmptyState } from "@/components/states/EmptyState";
import { useChatStore } from "@/store/chat";
import { useChatStream } from "@/hooks/useChatStream";

export function ChatThread({ session }: { session: ChatSession | null }) {
  const endRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);
  const messageCount = session?.messages.length ?? 0;
  const lastMessage = session?.messages[session.messages.length - 1];
  const { send } = useChatStream();

  useEffect(() => {
    if (!endRef.current) return;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // A new message arriving is a discrete event → animate smoothly. But while
    // a streaming message's content grows token-by-token (same count, just a
    // longer last message), a smooth scroll restarts on every token against a
    // moving target and visibly oscillates ("shakes"). Jump instantly instead
    // so the view stays pinned to the bottom without animation churn.
    const isNewMessage = messageCount !== prevCountRef.current;
    prevCountRef.current = messageCount;
    endRef.current.scrollIntoView({
      behavior: prefersReducedMotion || !isNewMessage ? "auto" : "smooth",
    });
  }, [messageCount, lastMessage?.content]);

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

          // While a long-running research turn is still streaming, show the
          // progress card ABOVE the bubble — status on top, then the papers it
          // finds (rendered inside the bubble), then the write-up. Suppress the
          // empty "…" bubble so we don't show two indicators at once.
          //
          // Once the result cards arrive, the finding phase is done and the
          // bubble's own "…" indicator takes over for the final write-up — so
          // drop the card to avoid a redundant third indicator.
          const intent = msg.routing_decision?.intent;
          const hasResults = !!msg.search_results && msg.search_results.length > 0;
          const showResearchCard =
            msg.role === "assistant" &&
            msg.status === "streaming" &&
            !hasResults &&
            (intent === "paper_search" || intent === "paper_suggest");

          return (
            <div key={`${msg.run_id ?? "user"}-${i}`} className="space-y-1">
              {showResearchCard && (
                <div className="pl-1">
                  <ResearchProgressCard intent={intent} trace={msg.trace} />
                </div>
              )}
              <MessageBubble
                message={msg}
                onRetry={retryHandler}
                backendSessionId={session.backend_session_id}
                researching={showResearchCard}
                onSendTurn={(text) => void send(session.id, text)}
              />
              {msg.role === "assistant" && msg.routing_decision && (
                <div className="flex justify-start pl-1">
                  <RoutingBadge decision={msg.routing_decision} />
                </div>
              )}
              {msg.role === "assistant" &&
                msg.run_id !== null &&
                session.backend_session_id !== null && (
                  <div className="pl-1">
                    <TraceInline
                      trace={msg.trace ?? []}
                      sessionId={session.backend_session_id}
                      runId={msg.run_id}
                    />
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
