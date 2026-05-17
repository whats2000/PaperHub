/**
 * A single chat message bubble.
 *
 * Phase A: renders plain text with inline §sec, p.N markers as <code>.
 * Phase B: clickable navigation to PDF page view.
 */

import type { Message as MessageType } from "../../store/chat";

interface Props {
  message: MessageType;
  isLoading?: boolean;
}

/** Replace [§section, p.N] markers with <code> spans. */
function renderContent(text: string): React.ReactNode {
  if (!text) return null;
  const parts = text.split(/(\\[§[^\]]+\\])/g);
  return parts.map((part, i) => {
    if (part.startsWith("[§")) {
      return (
        <code
          key={i}
          className="rounded bg-neutral-700 px-1 py-0.5 font-mono text-xs text-neutral-300"
        >
          {part}
        </code>
      );
    }
    return part;
  });
}

export function Message({ message, isLoading }: Props) {
  const isUser = message.role === "user";
  const isError = message.role === "error";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-prose rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "bg-blue-600 text-white"
            : isError
              ? "bg-red-900/50 text-red-300 border border-red-700"
              : "bg-neutral-800 text-neutral-100"
        }`}
      >
        {isLoading && !message.content ? (
          <span className="inline-flex items-center gap-1 text-neutral-400">
            <span className="animate-pulse">Thinking</span>
            <span className="animate-bounce delay-75">.</span>
            <span className="animate-bounce delay-150">.</span>
            <span className="animate-bounce delay-300">.</span>
          </span>
        ) : (
          renderContent(message.content)
        )}
      </div>
    </div>
  );
}
