/** The Notes pane's header: the paper's note, "This chat", and close. */
import { FileText, MessageSquare, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useEffect, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { notePanelId, noteTabId } from "./noteTabIds";

export type NoteTab = "paper" | "chat";

interface Props {
  active: NoteTab;
  /** Prefix of the tab and panel ids (the pane's `useId`). */
  idBase: string;
  /** The paper's title, or null when no paper is open (no paper tab). */
  paperTitle: string | null;
  chatTitle: string;
  /** False when the chat has no note worth a tab (it leaves with a motion). */
  chatAvailable: boolean;
  onSelect: (tab: NoteTab) => void;
  onClose: () => void;
}

/** How long the chat tab takes to leave (its exit animation). */
const CHAT_TAB_EXIT_MS = 200;

const TAB =
  "inline-flex min-w-0 max-w-60 items-center gap-1 rounded px-2 py-1 text-xs transition-colors duration-150 motion-reduce:transition-none";

export function NotesTabs({
  active,
  idBase,
  paperTitle,
  chatTitle,
  chatAvailable,
  onSelect,
  onClose,
}: Props) {
  const { t } = useTranslation("notes");
  // The chat tab stays mounted while it animates out.
  const [chatMounted, setChatMounted] = useState(chatAvailable);
  if (chatAvailable && !chatMounted) setChatMounted(true);
  useEffect(() => {
    if (chatAvailable || !chatMounted) return;
    const id = setTimeout(() => setChatMounted(false), CHAT_TAB_EXIT_MS);
    return () => clearTimeout(id);
  }, [chatAvailable, chatMounted]);
  const tabs: NoteTab[] = [
    ...(paperTitle !== null ? (["paper"] as const) : []),
    ...(chatAvailable ? (["chat"] as const) : []),
  ];

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const next = tabs[(tabs.indexOf(active) + step + tabs.length) % tabs.length];
    if (!step || tabs.length < 2 || next === undefined) return;
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
            id={noteTabId(idBase, "paper")}
            aria-controls={notePanelId(idBase)}
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
        {chatMounted && (
          <button
            type="button"
            role="tab"
            data-note-tab="chat"
            id={noteTabId(idBase, "chat")}
            aria-controls={notePanelId(idBase)}
            aria-selected={active === "chat"}
            tabIndex={active === "chat" ? 0 : -1}
            onClick={() => onSelect("chat")}
            title={t("pane.chatTitle", "Notes for this chat only: {{title}}", { title: chatTitle })}
            className={cn(
              tabClass("chat"),
              "shrink-0 fill-mode-forwards motion-reduce:animate-none",
              chatAvailable
                ? "animate-in fade-in-0 slide-in-from-right-2 duration-200"
                : "animate-out fade-out-0 slide-out-to-right-2 duration-200",
            )}
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
