import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { Components, ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Copy, RotateCcw, Undo2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

// KaTeX stylesheet — required for rehype-katex output to render correctly.
import "katex/dist/katex.min.css";

import type { ChatMessage, SearchResultCandidate } from "@/types/domain";
import { Button } from "@/components/ui/button";
import { LoadingDots } from "@/components/states/LoadingDots";
import { SearchResultList } from "@/components/chat/SearchResultList";
import { SqlCard } from "@/components/chat/SqlCard";
import { FeedbackButtons } from "@/components/chat/FeedbackButtons";
import { DeckChip } from "@/components/slides/DeckChip";
import { rehypeChunkCitations } from "@/lib/rehypeChunkCitations";
import { rehypeFigureCitations } from "@/lib/rehypeFigureCitations";
import { rehypeUnsourcedMarks } from "@/lib/unsourcedMarks";
import { rehypeTableBreaks } from "@/lib/rehypeTableBreaks";
import { normalizeMath, KATEX_MACROS } from "@/lib/normalizeMath";
import { liftSqlFence } from "@/lib/liftSqlFence";
import { CitationMarker } from "@/components/canvas/CitationMarker";
import { FigureCitationMarker } from "@/components/canvas/FigureCitationMarker";
import { ViewCitationMarker } from "@/components/canvas/ViewCitationMarker";
import { UnsourcedMarker } from "@/components/canvas/UnsourcedMarker";
import { AgentEventChip, type ChipEvent } from "@/components/chat/AgentEventChip";
import { ConfirmActionCard } from "@/components/chat/ConfirmActionCard";
import { ProposalButtons } from "@/components/chat/ProposalButtons";
import { splitProposals } from "@/lib/proposals";
import { listSessionReferences } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useMemoriesStore } from "@/store/memories";

interface Props {
  message: ChatMessage;
  onRetry?: () => void;
  backendSessionId?: number | null;
  /**
   * When the parent shows a dedicated in-flight indicator (the research
   * progress card), suppress this bubble's empty "…" placeholder so the user
   * doesn't see two loading affordances at once.
   */
  researching?: boolean;
  /**
   * Prefill the composer with an editable prompt (threaded down from
   * ChatThread). Forwarded to the DeckChip so its Generate/Edit affordances
   * drop a starter prompt into the input instead of sending immediately.
   */
  onPrefill?: (message: string) => void;
  /** Fork this (user) message: branch a new session from the point above it
   *  and prefill the composer with this message. Only shown on user messages. */
  onFork?: () => void;
  /** Send a proposal button's text as a new message (agent turns). */
  onSend?: (text: string) => void;
  /** A turn is in flight: proposal buttons are disabled. */
  sendDisabled?: boolean;
  /** Merge a patch into this message's agent UI event at `index` (a confirm
   *  card's status/result, a chip's undone flag). */
  onPatchEvent?: (index: number, patch: Record<string, unknown>) => void;
}

/** After a confirmed action ran, refresh the panels it may have changed. */
function refreshAfterAction(sessionId: number | null | undefined): void {
  if (sessionId == null) return;
  void listSessionReferences(sessionId)
    .then((refs) => useChatStore.getState().setReferences(sessionId, refs))
    .catch(() => undefined);
  void useMemoriesStore
    .getState()
    .fetchMemories(sessionId)
    .catch(() => undefined);
}

export function MessageBubble({
  message,
  onRetry,
  backendSessionId,
  researching = false,
  onPrefill,
  onFork,
  onSend,
  sendDisabled = false,
  onPatchEvent,
}: Props) {
  const { t } = useTranslation("chat");
  const { t: tStates } = useTranslation("states");
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const isOk = message.status === "ok" || (isAssistant && message.status === undefined);
  const isStreaming = message.status === "streaming" || message.status === "processing";
  const isError = message.status === "error";
  const isInterrupted = message.status === "interrupted";
  const isStopped = message.status === "stopped";
  const isStreamingEmpty =
    isStreaming && !splitProposals(message.content).body;
  const isStreamingWithContent = isStreaming && !isStreamingEmpty;
  // Copy is offered on completed assistant messages AND on the user's own
  // input bubbles (always copyable). Fork (rollback) is user-only.
  const canCopy =
    (isAssistant && isOk && !isStreaming) || (isUser && !!message.content);
  const showFork = isUser && !!onFork;
  // Thumbs feedback (H2) is keyed by run id: completed answers only.
  const feedbackRunId =
    isAssistant && isOk && !isStreaming ? message.run_id : null;
  const hasSearchResults =
    isAssistant &&
    message.search_results !== undefined &&
    message.search_results.length > 0;
  // One list per search (agent turns may search several times); a pipeline
  // turn's single list otherwise.
  const searchBatches: SearchResultCandidate[][] = !hasSearchResults
    ? []
    : message.search_result_batches && message.search_result_batches.length > 0
      ? message.search_result_batches.filter((b) => b.length > 0)
      : [message.search_results!];
  // Agent answers keep their <proposal> lines in the stored text (R12); show
  // them as buttons, not markdown. Proposal buttons only on a finished turn.
  const { body, proposals } = isAssistant
    ? splitProposals(message.content)
    : { body: message.content, proposals: [] as string[] };
  const agentEvents = isAssistant ? (message.agent_events ?? []) : [];
  const chipEvents = agentEvents
    .map((ev, index) => ({ ev, index }))
    .filter(
      (x): x is { ev: ChipEvent; index: number } =>
        x.ev.event === "paper_added" || x.ev.event === "memory_saved",
    );
  const confirmEvents = agentEvents
    .map((ev, index) => ({ ev, index }))
    .filter((x) => x.ev.event === "confirm_request");

  // The research card owns the waiting state; nothing to show in the bubble
  // until content or search results arrive.
  if (researching && isStreamingEmpty && !hasSearchResults) {
    return null;
  }

  return (
    <article
      data-role={message.role}
      // Reserve space under a user bubble so its hover action row (absolute
      // -bottom-7) sits within the message's own section, not over the next one.
      className={`flex w-full ${isUser ? "justify-end pb-6" : "justify-start"}`}
    >
      <div
        className={`group/bubble relative ${
          isUser ? "max-w-[80%]" : "w-full pl-1 pr-8 sm:pl-2 sm:pr-12"
        } ${isStreamingEmpty ? "min-w-[64px]" : ""}`}
      >
        <div
          className={
            isUser
              ? // A soft grey, as chat UIs do: the primary colour is near-white in
                // dark mode and near-black in light, the loudest block on screen.
                "rounded-2xl px-4 py-2 prose prose-sm dark:prose-invert bg-secondary text-secondary-foreground"
              : // Assistant: full-width, no bubble — markdown, cards and traces get
                // the whole reading column (ChatGPT/Claude convention).
                "prose prose-sm max-w-none dark:prose-invert"
          }
        >
          {isError ? (
            <div className="space-y-2">
              <p className="text-destructive">{message.error}</p>
              {onRetry && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onRetry}
                  className="gap-2"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  {t("bubble.retry")}
                </Button>
              )}
            </div>
          ) : isInterrupted ? (
            <div className="space-y-2">
              <p className="text-muted-foreground italic">
                {t("bubble.interrupted", "Generation was interrupted.")}
              </p>
              {onRetry && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onRetry}
                  className="gap-2"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  {t("bubble.retry")}
                </Button>
              )}
            </div>
          ) : isStopped ? (
            <p
              data-testid="stopped-turn"
              className="text-muted-foreground italic animate-in fade-in-0 duration-200 motion-reduce:animate-none"
            >
              {t("bubble.stopped", "Stopped. What already ran is below; you can undo it.")}
            </p>
          ) : isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : isStreamingEmpty ? (
            // Pre-token waiting state — tight three-dot cluster so it reads
            // as a real "…" typing indicator, not stretched apart.
            <LoadingDots ariaLabel={tStates("loading.streaming")} />
          ) : (
            // react-markdown renders to React elements (no dangerouslySetInnerHTML).
            // Raw HTML in source is not rendered as HTML by default — exactly what
            // we want for arbitrary tool-result strings flowing into assistant content.
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[
                [rehypeKatex, { macros: KATEX_MACROS }],
                rehypeChunkCitations,
                rehypeFigureCitations,
                rehypeUnsourcedMarks,
                rehypeTableBreaks,
              ]}
              components={{
                // A ```sql fenced block (the SQL a library_stats turn ran) is
                // lifted out of the prose into a distinct collapsible card.
                pre: ({ node, children }: ExtraProps & { children?: ReactNode }) => {
                  const codeEl = node?.children?.[0] as
                    | { properties?: { className?: unknown }; children?: { value?: string }[] }
                    | undefined;
                  const cls = Array.isArray(codeEl?.properties?.className)
                    ? (codeEl.properties.className as string[]).join(" ")
                    : "";
                  if (/\blanguage-sql\b/.test(cls)) {
                    const sql = (codeEl?.children?.[0]?.value ?? "").replace(/\n+$/, "");
                    return <SqlCard sql={sql} />;
                  }
                  return <pre>{children}</pre>;
                },
                "fig-cite": ({ node }: ExtraProps) => {
                  const props = (node?.properties ?? {}) as {
                    dataPaper?: number | string;
                    dataFigure?: string;
                    dataOrdinal?: number | string;
                  };
                  return (
                    <FigureCitationMarker
                      paper={Number(props.dataPaper)}
                      figureId={String(props.dataFigure ?? "")}
                      ordinal={Number(props.dataOrdinal)}
                    />
                  );
                },
                "unsourced-mark": ({ node }: ExtraProps) => {
                  const props = (node?.properties ?? {}) as { dataReason?: string };
                  return <UnsourcedMarker reason={String(props.dataReason ?? "")} />;
                },
                "view-cite": ({ node }: ExtraProps) => {
                  const props = (node?.properties ?? {}) as {
                    dataPaper?: number | string;
                    dataPage?: number | string;
                    dataBox?: string;
                    dataOrdinal?: number | string;
                  };
                  const b = String(props.dataBox ?? "").split(",").map(Number);
                  return (
                    <ViewCitationMarker
                      paper={Number(props.dataPaper)}
                      page={Number(props.dataPage)}
                      box={[b[0] ?? 0, b[1] ?? 0, b[2] ?? 0, b[3] ?? 0]}
                      ordinal={Number(props.dataOrdinal)}
                    />
                  );
                },
                "chunk-cite": ({ node }: ExtraProps) => {
                  const props = (node?.properties ?? {}) as {
                    dataChunkId?: number | string;
                    dataOrdinal?: number | string;
                  };
                  return (
                    <CitationMarker
                      chunkId={Number(props.dataChunkId)}
                      ordinal={Number(props.dataOrdinal)}
                    />
                  );
                },
              } as Components}
            >
              {normalizeMath(liftSqlFence(body)) || " "}
            </ReactMarkdown>
          )}
          {message.stopping && (
            // Stop pressed: the turn stays put until the backend says whether
            // it had already acted (kept as a stub) or is retracted.
            <p
              data-testid="stopping-turn"
              className="not-prose mt-1 text-xs italic text-muted-foreground animate-in fade-in-0 duration-200 motion-reduce:animate-none"
            >
              {t("bubble.stopping", "Stopping…")}
            </p>
          )}
          {isStreamingWithContent && !message.stopping && (
            // Cursor blink trailing the streamed content — subtler than dots
            // once real text is visible.
            <span
              aria-label="streaming"
              className="inline-block w-[2px] h-4 ml-0.5 align-[-2px] bg-muted-foreground motion-safe:animate-pulse"
            />
          )}
        </div>

        {/* Search results — rendered below the bubble body.
            sessionId may be transiently null in the race window between
            the assistant placeholder render and the `session` SSE event
            populating backend_session_id; SearchResultList handles that
            by disabling the Add button until the id arrives. */}
        {searchBatches.map((candidates, i) => (
          <SearchResultList
            key={i}
            candidates={candidates}
            sessionId={backendSessionId ?? null}
          />
        ))}

        {/* Agent "do, show, undo" chips (papers added, memories saved). */}
        {chipEvents.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {chipEvents.map(({ ev, index }) => (
              <AgentEventChip
                key={index}
                ev={ev}
                sessionId={backendSessionId ?? null}
                onChange={(patch) => onPatchEvent?.(index, patch)}
              />
            ))}
          </div>
        )}

        {/* Confirm-tier actions waiting for the user. */}
        {confirmEvents.map(({ ev, index }) =>
          ev.event === "confirm_request" ? (
            <ConfirmActionCard
              key={ev.data.action_id}
              data={ev.data}
              onChange={(patch) => onPatchEvent?.(index, patch)}
              onDone={() => refreshAfterAction(backendSessionId)}
            />
          ) : null,
        )}

        {/* Deck chip — rendered when a slide deck has been generated for this
            turn (deck SSE event). Shows below search results if both exist. */}
        {isAssistant && message.deck !== undefined && (
          <DeckChip deck={message.deck} onPrefill={onPrefill} />
        )}

        {isAssistant && !isStreaming && onSend && (
          <ProposalButtons
            proposals={proposals}
            onSend={onSend}
            disabled={sendDisabled}
          />
        )}

        {/* Hover-revealed action row under the bubble, bottom-right — the same
            affordance for both roles. Assistant: Copy. User's own input: Copy +
            Fork (Undo2 curved back-arrow = "roll back to this point", distinct
            from the Retry button's RotateCcw refresh loop and from a pencil,
            which would imply destructive in-place editing). */}
        {(canCopy || showFork) && (
          <div
            className={`opacity-0 group-hover/bubble:opacity-100 focus-within:opacity-100 has-[[data-popup-open]]:opacity-100 has-[[aria-pressed=true]]:opacity-100 transition-opacity absolute -bottom-6 flex gap-1 ${
              isUser ? "right-0" : "right-8 sm:right-12"
            }`}
          >
            {canCopy && (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-6 w-6"
                aria-label={t("bubble.copy")}
                onClick={() => {
                  navigator.clipboard.writeText(body).then(
                    () => toast.success(t("toast.copied")),
                    () => toast.error(t("toast.copyFailed")),
                  );
                }}
              >
                <Copy className="h-3.5 w-3.5" />
              </Button>
            )}
            {feedbackRunId !== null && <FeedbackButtons runId={feedbackRunId} />}
            {showFork && (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-6 w-6"
                aria-label={t("bubble.fork")}
                title={t("bubble.forkTitle")}
                onClick={onFork}
              >
                <Undo2 className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
