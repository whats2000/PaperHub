import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Copy, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import type { ChatMessage } from "@/types/domain";
import { Button } from "@/components/ui/button";

interface Props {
  message: ChatMessage;
  onRetry?: () => void;
}

export function MessageBubble({ message, onRetry }: Props) {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const isOk = message.status === "ok" || (isAssistant && message.status === undefined);
  const isStreaming = message.status === "streaming";
  const isError = message.status === "error";
  const showCopy = isAssistant && isOk && !isStreaming;

  return (
    <article
      data-role={message.role}
      className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div className="group/bubble relative">
        <div
          className={`max-w-[80%] rounded-2xl px-4 py-2 prose prose-sm dark:prose-invert ${
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
          ) : (
            // react-markdown renders to React elements (no dangerouslySetInnerHTML).
            // Raw HTML in source is not rendered as HTML by default — exactly what
            // we want for arbitrary tool-result strings flowing into assistant content.
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content || " "}
            </ReactMarkdown>
          )}
          {isStreaming && (
            <span aria-label="streaming" className="inline-flex ml-2 gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground motion-safe:animate-pulse" />
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground motion-safe:animate-pulse [animation-delay:120ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground motion-safe:animate-pulse [animation-delay:240ms]" />
            </span>
          )}
        </div>

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
