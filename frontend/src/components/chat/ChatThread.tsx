import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import type { ChatSession } from "@/types/domain";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { RoutingBadge } from "@/components/chat/RoutingBadge";
import { ResearchProgressCard } from "@/components/chat/ResearchProgressCard";
import { SlideProgressCard } from "@/components/chat/SlideProgressCard";
import { TraceInline } from "@/components/chat/TraceInline";
import { EmptyState } from "@/components/states/EmptyState";
import { forkSession } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useChatStream } from "@/hooks/useChatStream";

export function ChatThread({ session }: { session: ChatSession | null }) {
  const { t } = useTranslation("chat");
  const endRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);
  const messageCount = session?.messages.length ?? 0;
  const lastMessage = session?.messages[session.messages.length - 1];
  const { send } = useChatStream();
  const requestComposerText = useChatStore((s) => s.requestComposerText);

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

  const forkFrom = useCallback(
    async (runId: number) => {
      if (!session || session.backend_session_id === null) return;
      try {
        const res = await forkSession(session.backend_session_id, runId);
        const store = useChatStore.getState();
        // Pass the parent's backend id so the sidebar groups the fork under it
        // immediately (before the next GET /sessions sync confirms it).
        store.addForkedSession(
          res.session_id,
          res.title,
          session.backend_session_id,
        );
        // Prefill the forked message — editable, NOT sent (edit = re-prompt;
        // send unchanged = retry). requestComposerText focuses the composer.
        store.requestComposerText(res.forked_message);
      } catch (err) {
        console.warn("[ChatThread] fork failed:", err);
        toast.error(t("toast.forkFailed"));
      }
    },
    [session, t],
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

          // A user message can fork. Its turn run_id is the message's own
          // run_id, or — for a just-sent turn not yet hydrated — the paired
          // assistant message's. Hidden when neither is known.
          let forkHandler: (() => void) | undefined;
          if (msg.role === "user" && session.backend_session_id !== null) {
            const runId = msg.run_id ?? session.messages[i + 1]?.run_id ?? null;
            if (runId !== null) {
              const captured = runId;
              forkHandler = () => void forkFrom(captured);
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

          // The slide card owns the waiting state for a `slides` GENERATE turn
          // until the `deck` event lands (message.deck set). Edit/notes
          // follow-ups already have a deck attached, so the card stays hidden.
          const showSlideCard =
            msg.role === "assistant" &&
            msg.status === "streaming" &&
            intent === "slides" &&
            !msg.deck;

          return (
            <div
              key={`${msg.run_id ?? "user"}-${i}`}
              // Match the user bubble's reserved bottom space on the assistant
              // section so the area under the trace breathes the same way.
              className={`space-y-1${msg.role === "assistant" ? " pb-6" : ""}`}
            >
              {showResearchCard && (
                <div className="pl-1">
                  <ResearchProgressCard intent={intent} trace={msg.trace} />
                </div>
              )}
              {showSlideCard && (
                <div className="pl-1">
                  <SlideProgressCard trace={msg.trace} />
                </div>
              )}
              <MessageBubble
                message={msg}
                onRetry={retryHandler}
                backendSessionId={session.backend_session_id}
                researching={showResearchCard || showSlideCard}
                onPrefill={requestComposerText}
                onFork={forkHandler}
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
