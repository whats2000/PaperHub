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

import type { ChatMessage } from "@/types/domain";
import { Button } from "@/components/ui/button";
import { LoadingDots } from "@/components/states/LoadingDots";
import { SearchResultList } from "@/components/chat/SearchResultList";
import { SqlCard } from "@/components/chat/SqlCard";
import { DeckChip } from "@/components/slides/DeckChip";
import { rehypeChunkCitations } from "@/lib/rehypeChunkCitations";
import { normalizeMath, KATEX_MACROS } from "@/lib/normalizeMath";
import { liftSqlFence } from "@/lib/liftSqlFence";
import { CitationMarker } from "@/components/canvas/CitationMarker";

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
}

export function MessageBubble({
  message,
  onRetry,
  backendSessionId,
  researching = false,
  onPrefill,
  onFork,
}: Props) {
  const { t } = useTranslation("chat");
  const { t: tStates } = useTranslation("states");
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const isOk = message.status === "ok" || (isAssistant && message.status === undefined);
  const isStreaming = message.status === "streaming" || message.status === "processing";
  const isError = message.status === "error";
  const isInterrupted = message.status === "interrupted";
  const isStreamingEmpty = isStreaming && !message.content;
  const isStreamingWithContent = isStreaming && !!message.content;
  // Copy is offered on completed assistant messages AND on the user's own
  // input bubbles (always copyable). Fork (rollback) is user-only.
  const canCopy =
    (isAssistant && isOk && !isStreaming) || (isUser && !!message.content);
  const showFork = isUser && !!onFork;
  const hasSearchResults =
    isAssistant &&
    message.search_results !== undefined &&
    message.search_results.length > 0;

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
              ? "rounded-2xl px-4 py-2 prose prose-sm dark:prose-invert bg-primary text-primary-foreground"
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
              rehypePlugins={[[rehypeKatex, { macros: KATEX_MACROS }], rehypeChunkCitations]}
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
              {normalizeMath(liftSqlFence(message.content)) || " "}
            </ReactMarkdown>
          )}
          {isStreamingWithContent && (
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
        {hasSearchResults && (
          <SearchResultList
            candidates={message.search_results!}
            sessionId={backendSessionId ?? null}
          />
        )}

        {/* Deck chip — rendered when a slide deck has been generated for this
            turn (deck SSE event). Shows below search results if both exist. */}
        {isAssistant && message.deck !== undefined && (
          <DeckChip deck={message.deck} onPrefill={onPrefill} />
        )}

        {/* Hover-revealed action row under the bubble, bottom-right — the same
            affordance for both roles. Assistant: Copy. User's own input: Copy +
            Fork (Undo2 curved back-arrow = "roll back to this point", distinct
            from the Retry button's RotateCcw refresh loop and from a pencil,
            which would imply destructive in-place editing). */}
        {(canCopy || showFork) && (
          <div
            className={`opacity-0 group-hover/bubble:opacity-100 focus-within:opacity-100 transition-opacity absolute -bottom-6 flex gap-1 ${
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
                  navigator.clipboard.writeText(message.content).then(
                    () => toast.success(t("toast.copied")),
                    () => toast.error(t("toast.copyFailed")),
                  );
                }}
              >
                <Copy className="h-3.5 w-3.5" />
              </Button>
            )}
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
