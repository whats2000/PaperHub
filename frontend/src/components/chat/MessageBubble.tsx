import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Copy, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import type { ChatMessage } from "@/types/domain";
import { Button } from "@/components/ui/button";
import { LoadingDots } from "@/components/states/LoadingDots";
import { SearchResultList } from "@/components/chat/SearchResultList";

interface Props {
  message: ChatMessage;
  onRetry?: () => void;
  backendSessionId?: number | null;
}

export function MessageBubble({ message, onRetry, backendSessionId }: Props) {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const isOk = message.status === "ok" || (isAssistant && message.status === undefined);
  const isStreaming = message.status === "streaming";
  const isError = message.status === "error";
  const isStreamingEmpty = isStreaming && !message.content;
  const isStreamingWithContent = isStreaming && !!message.content;
  const showCopy = isAssistant && isOk && !isStreaming;
  const hasSearchResults =
    isAssistant &&
    message.search_results !== undefined &&
    message.search_results.length > 0;

  return (
    <article
      data-role={message.role}
      className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div className={`group/bubble relative max-w-[80%] ${isStreamingEmpty ? "min-w-[64px]" : ""}`}>
        <div
          className={`rounded-2xl px-4 py-2 prose prose-sm dark:prose-invert ${
            isUser ? "bg-primary text-primary-foreground" : "bg-card border border-border"
          }`}
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
                  Retry
                </Button>
              )}
            </div>
          ) : isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : isStreamingEmpty ? (
            // Pre-token waiting state — tight three-dot cluster so it reads
            // as a real "…" typing indicator, not stretched apart.
            <LoadingDots ariaLabel="streaming" />
          ) : (
            // react-markdown renders to React elements (no dangerouslySetInnerHTML).
            // Raw HTML in source is not rendered as HTML by default — exactly what
            // we want for arbitrary tool-result strings flowing into assistant content.
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content || " "}
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

        {/* Copy button — hover-revealed on completed assistant messages */}
        {showCopy && (
          <div className="opacity-0 group-hover/bubble:opacity-100 focus-within:opacity-100 transition-opacity absolute -bottom-7 right-0 flex gap-1">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-6 w-6"
              aria-label="Copy message"
              onClick={() => {
                navigator.clipboard.writeText(message.content).then(
                  () => toast.success("Copied to clipboard"),
                  () => toast.error("Copy failed"),
                );
              }}
            >
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </div>
    </article>
  );
}
