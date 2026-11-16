/** The Notes pane's header: the paper's note, "This chat", and close. */
import { FileText, MessageSquare, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type NoteTab = "paper" | "chat";

interface Props {
  active: NoteTab;
  /** The paper's title, or null when no paper is open (no paper tab). */
  paperTitle: string | null;
  chatTitle: string;
  /** False when there is no chat to keep notes for. */
  chatAvailable: boolean;
  onSelect: (tab: NoteTab) => void;
  onClose: () => void;
}

const TAB =
  "inline-flex min-w-0 max-w-60 items-center gap-1 rounded px-2 py-1 text-xs transition-colors duration-150 motion-reduce:transition-none";

export function NotesTabs({ active, paperTitle, chatTitle, chatAvailable, onSelect, onClose }: Props) {
  const { t } = useTranslation("notes");
  const tabs: NoteTab[] = [
    ...(paperTitle !== null ? (["paper"] as const) : []),
    ...(chatAvailable ? (["chat"] as const) : []),
  ];

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = tabs[(tabs.indexOf(active) + 1) % tabs.length];
    if (tabs.length < 2 || next === undefined) return;
    e.preventDefault();
    onSelect(next);
    e.currentTarget.querySelector<HTMLButtonElement>(`[data-note-tab="${next}"]`)?.focus();
  };

  const tabClass = (tab: NoteTab) =>
    cn(
      TAB,
      active === tab
        ? "bg-foreground/10 font-medium text-foreground"
        : "text-muted-foreground hover:bg-muted hover:text-foreground",
    );

  return (
    <div className="flex shrink-0 items-center gap-1 border-b border-border px-2 py-1">
      <div
        role="tablist"
        aria-label={t("pane.label", "Notes")}
        className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden"
        onKeyDown={onKeyDown}
      >
        {paperTitle !== null && (
          <button
            type="button"
            role="tab"
            data-note-tab="paper"
            aria-selected={active === "paper"}
            tabIndex={active === "paper" ? 0 : -1}
            onClick={() => onSelect("paper")}
            title={t("pane.paperTitle", "Notes on: {{title}}", { title: paperTitle })}
            className={tabClass("paper")}
          >
            <FileText className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">{paperTitle}</span>
          </button>
        )}
        {chatAvailable && (
          <button
            type="button"
            role="tab"
            data-note-tab="chat"
            aria-selected={active === "chat"}
            tabIndex={active === "chat" ? 0 : -1}
            onClick={() => onSelect("chat")}
            title={t("pane.chatTitle", "Notes for this chat only: {{title}}", { title: chatTitle })}
            className={cn(tabClass("chat"), "shrink-0")}
          >
            <MessageSquare className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">{t("pane.chat", "This chat")}</span>
          </button>
        )}
      </div>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7 shrink-0"
        aria-label={t("pane.close", "Close notes")}
        onClick={onClose}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}
