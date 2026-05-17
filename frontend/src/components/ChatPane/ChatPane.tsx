/**
 * Main chat panel — composes Message list, RoutingBadge, TraceInline, Composer.
 */

import { useEffect, useRef } from "react";
import { useChatStore } from "../../store/chat";
import { Composer } from "./Composer";
import { Message } from "./Message";
import { RoutingBadge } from "./RoutingBadge";
import { TraceInline } from "./TraceInline";

export function ChatPane() {
  const messages = useChatStore((s) => s.messages);
  const routingDecision = useChatStore((s) => s.routingDecision);
  const traceSteps = useChatStore((s) => s.traceSteps);
  const isLoading = useChatStore((s) => s.isLoading);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    if (bottomRef.current && typeof bottomRef.current.scrollIntoView === "function") {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  const isEmpty = messages.length === 0;

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
        {isEmpty ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <h2 className="text-2xl font-semibold text-neutral-300">Welcome to PaperHub</h2>
            <p className="mt-2 max-w-md text-sm text-neutral-500">
              Import papers using <code className="rounded bg-neutral-800 px-1">POST /papers/import</code>,
              then ask questions about their content.
            </p>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4">
            {/* Show routing badge + trace before the last assistant message */}
            {routingDecision && (
              <div className="flex items-center gap-2">
                <RoutingBadge decision={routingDecision} />
              </div>
            )}
            {traceSteps.length > 0 && <TraceInline steps={traceSteps} />}

            {messages.map((msg) => (
              <Message
                key={msg.id}
                message={msg}
                isLoading={isLoading && msg.role === "assistant" && !msg.content}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Composer */}
      <Composer />
    </main>
  );
}
