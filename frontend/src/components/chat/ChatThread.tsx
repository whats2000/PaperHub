import { useEffect, useRef } from "react";

import type { ChatSession } from "@/types/domain";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { RoutingBadge } from "@/components/chat/RoutingBadge";
import { TraceInline } from "@/components/chat/TraceInline";
import { EmptyState } from "@/components/states/EmptyState";
import { ScrollArea } from "@/components/ui/scroll-area";

export function ChatThread({ session }: { session: ChatSession | null }) {
  const endRef = useRef<HTMLDivElement>(null);
  const lastMessage = session?.messages[session.messages.length - 1];

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [session?.messages.length, lastMessage?.content]);

  if (!session || session.messages.length === 0) {
    return <EmptyState />;
  }

  return (
    <ScrollArea className="flex-1">
      <div className="max-w-3xl mx-auto p-4 space-y-4">
        {session.messages.map((msg, i) => (
          <div key={`${msg.run_id ?? "user"}-${i}`} className="space-y-1">
            <MessageBubble message={msg} />
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
        ))}
        <div ref={endRef} />
      </div>
    </ScrollArea>
  );
}
