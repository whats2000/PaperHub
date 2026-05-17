/**
 * Message input composer — textarea + send button.
 *
 * Calls streamChat (via the zustand store's sendMessage) on submit.
 * Supports Enter to send (Shift+Enter for newline).
 */

import { useRef, useState } from "react";
import { useChatStore } from "../../store/chat";

export function Composer() {
  const [text, setText] = useState("");
  const sendMessage = useChatStore((s) => s.sendMessage);
  const isLoading = useChatStore((s) => s.isLoading);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function handleSubmit() {
    const trimmed = text.trim();
    if (!trimmed || isLoading) return;
    sendMessage(trimmed);
    setText("");
    textareaRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div className="flex items-end gap-3 border-t border-neutral-800 bg-neutral-950 px-4 py-3">
      <textarea
        ref={textareaRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask a question about your papers…"
        rows={1}
        disabled={isLoading}
        className="flex-1 resize-none rounded-xl border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-sm text-neutral-100 placeholder-neutral-500 outline-none transition focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
        style={{ maxHeight: "8rem", overflowY: "auto" }}
        aria-label="Message input"
      />
      <button
        onClick={handleSubmit}
        disabled={!text.trim() || isLoading}
        className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-500 disabled:opacity-40"
        aria-label="Send message"
      >
        {isLoading ? (
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" className="opacity-25" />
            <path d="M4 12a8 8 0 018-8" strokeLinecap="round" className="opacity-75" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
          </svg>
        )}
      </button>
    </div>
  );
}
