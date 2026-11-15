import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import type { ChatMessage, ChatSession } from "@/types/domain";
import { MessageBubble, SENT_SEARCH_PREFIX } from "@/components/chat/MessageBubble";
import { NeedsOkPill } from "@/components/chat/NeedsOkPill";
import { RoutingBadge } from "@/components/chat/RoutingBadge";
import { ResearchProgressCard } from "@/components/chat/ResearchProgressCard";
import { SlideProgressCard } from "@/components/chat/SlideProgressCard";
import { TraceInline } from "@/components/chat/TraceInline";
import { EmptyState } from "@/components/states/EmptyState";
import { forkSession } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useFeedbackStore } from "@/store/feedback";
import { useChatStream } from "@/hooks/useChatStream";

export function ChatThread({
  session,
  onSend,
  onSendHeld,
}: {
  session: ChatSession | null;
  /** Send a message as the user would (proposal buttons). Defaults to this
   *  thread's own stream; ChatPage passes its submit so Stop reaches it. */
  onSend?: (text: string) => void;
  /** Run an approved held search (H5a) as a follow-up turn; ChatPage passes
   *  its own stream so Stop reaches it. Rejects on a 409. */
  onSendHeld?: (heldId: string, text: string) => Promise<void>;
}) {
  const { t } = useTranslation("chat");
  const endRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);
  const messageCount = session?.messages.length ?? 0;
  const lastMessage = session?.messages[session.messages.length - 1];
  const { send } = useChatStream();
  const requestComposerText = useChatStore((s) => s.requestComposerText);
  const backendSessionId = session?.backend_session_id ?? null;

  // Restore the thumbs (H2) whenever the active session changes.
  useEffect(() => {
    if (backendSessionId === null) return;
    useFeedbackStore
      .getState()
      .hydrate(backendSessionId)
      .catch(() => undefined); // Thumbs just start unpressed.
  }, [backendSessionId]);

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
    (errorIndex: number, userContent: string, heldId?: string) => {
      if (!session) return;
      // Remove the failed assistant message; send will append a new one.
      useChatStore.getState().removeMessage(session.id, errorIndex);
      // Don't remove the user message — keep one user bubble, re-send. A
      // held-search follow-up posts its approval with an empty message.
      void send(session.id, heldId ? "" : userContent, {
        skipUserAppend: true,
        ...(heldId ? { approvedHeldId: heldId } : {}),
      });
    },
    [session, send],
  );

  // A failed "Sent search:" turn with no recorded approval cannot be re-run:
  // remove it (the card's Run button remains) instead of resending text.
  const dropHeldTurn = useCallback(
    (assistantIndex: number, userIndex: number) => {
      if (!session) return;
      const store = useChatStore.getState();
      store.removeMessage(session.id, assistantIndex);
      if (userIndex >= 0) store.removeMessage(session.id, userIndex);
      toast.error(
        t("outbound.held.retryUnavailable", "That search can't be retried here. Use Run the search on its card."),
      );
    },
    [session, t],
  );

  const sendText = useCallback(
    (text: string) => {
      if (onSend) {
        onSend(text);
        return;
      }
      if (!session) return;
      send(session.id, text).catch((err: unknown) => {
        console.warn("[ChatThread] send failed:", err);
      });
    },
    [onSend, session, send],
  );

  const runHeld = useCallback(
    (heldId: string, text: string): Promise<void> => {
      if (onSendHeld) return onSendHeld(heldId, text);
      if (!session) return Promise.resolve();
      return send(session.id, "", {
        approvedHeldId: heldId,
        userBubble: `${SENT_SEARCH_PREFIX}${text}`,
      });
    },
    [onSendHeld, session, send],
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

  const turnInFlight = session.messages.some(
    (m) => m.status === "streaming" || m.status === "processing",
  );
  const patchEvent = useChatStore.getState().patchAgentEvent;
  // Texts of the "Sent search:" turns already in this chat (a sent held card
  // without one was approved but never run).
  const followUpTexts = session.messages
    .filter((m) => m.role === "user" && m.content.startsWith(SENT_SEARCH_PREFIX))
    .map((m) => m.content.slice(SENT_SEARCH_PREFIX.length));
  // Proposals are follow-ups to the latest answer only.
  const latestAssistant = session.messages
    .map((m) => m.role)
    .lastIndexOf("assistant");

  // Cards still waiting for the user's OK in this chat, in thread order (the
  // pill): each bubble shows its held searches, then its Ask-first actions.
  const waitingCardIds = session.messages.flatMap((m) => {
    const events = m.agent_events ?? [];
    return [
      ...events.flatMap((ev) =>
        ev.event === "outbound_held" && (ev.data.status ?? "pending") === "pending"
          ? [ev.data.held_id]
          : [],
      ),
      ...events.flatMap((ev) =>
        ev.event === "confirm_request" && (ev.data.status ?? "pending") === "pending"
          ? [ev.data.action_id]
          : [],
      ),
    ];
  });

  return (
    <div className="relative flex flex-1 min-h-0 flex-col">
      <div ref={scrollerRef} className="flex-1 min-h-0 overflow-y-auto">
        <div
          className="max-w-3xl mx-auto p-4 space-y-4 pb-12"
          aria-live="polite"
          aria-atomic="false"
        >
          {session.messages.map((msg, i) => {
            // For error assistant messages, find the preceding user message for retry.
            let retryHandler: (() => void) | undefined;
            if (msg.role === "assistant" && (msg.status === "error" || msg.status === "interrupted")) {
              let userContent: string | undefined;
              let userMessage: ChatMessage | undefined;
              let userIndex = -1;
              for (let j = i - 1; j >= 0; j--) {
                if (session.messages[j]?.role === "user") {
                  userMessage = session.messages[j]!;
                  userIndex = j;
                  userContent = userMessage.content;
                  break;
                }
              }
              if (userContent !== undefined) {
                const capturedIndex = i;
                const capturedContent = userContent;
                // A held-search follow-up re-runs ONLY through its own recorded
                // approval; without one nothing is resent as plain text.
                const heldId = userMessage?.approved_held_id ?? undefined;
                const isHeldTurn =
                  heldId !== undefined || capturedContent.startsWith(SENT_SEARCH_PREFIX);
                const capturedUser = userIndex;
                retryHandler =
                  isHeldTurn && heldId === undefined
                    ? () => dropHeldTurn(capturedIndex, capturedUser)
                    : () => retryFrom(capturedIndex, capturedContent, heldId);
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
                // A stopped turn collapses toward where it was (height, gap and
                // opacity, 200 ms) before it is removed, so the thread glides.
                data-exiting={msg.exiting ? "true" : undefined}
                data-testid={msg.exiting ? "exiting-message" : undefined}
                className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity,margin] duration-200 ease-out data-[exiting=true]:mt-0! data-[exiting=true]:grid-rows-[0fr] data-[exiting=true]:opacity-0 motion-reduce:transition-none"
              >
                <div
                  // Match the user bubble's reserved bottom space on the assistant
                  // section so the area under the trace breathes the same way.
                  className={`min-h-0 space-y-1${msg.role === "assistant" ? " pb-6" : ""}${msg.exiting ? " overflow-hidden" : ""}`}
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
                    onSend={i === latestAssistant ? sendText : undefined}
                    sendDisabled={turnInFlight}
                    followUpTexts={followUpTexts}
                    onRunHeld={runHeld}
                    onPatchEvent={
                      msg.run_id !== null
                        ? (index, patch) =>
                            patchEvent(session.id, msg.run_id!, index, patch)
                        : undefined
                    }
                  />
                  {/* Pipeline (pre-H4) turns keep their routing badge; agent
                      turns show the turn summary, which also toggles the trace. */}
                  {msg.role === "assistant" &&
                    !msg.agent_summary &&
                    msg.routing_decision && (
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
                          paused={msg.paused}
                          // Agent turns (H4+; pipeline turns carry a routing
                          // decision) show the activity timeline (FR-02).
                          agent={
                            msg.agent_summary || !msg.routing_decision
                              ? {
                                  summary: msg.agent_summary ?? null,
                                  status: msg.status,
                                  writing: msg.content.length > 0,
                                }
                              : undefined
                          }
                        />
                      </div>
                    )}
                </div>
              </div>
            );
          })}
          <div ref={endRef} />
        </div>
      </div>
      <NeedsOkPill scrollerRef={scrollerRef} cardIds={waitingCardIds} />
    </div>
  );
}
