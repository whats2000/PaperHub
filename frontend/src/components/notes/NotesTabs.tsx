/** The Notes pane's header: the chat's paper tabs (the same set the reader
 * shows), "This chat", and close. */
import { MessageSquare, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";

import { PaperTabs, type OutsidePaperTabItem, type PaperTabItem } from "@/components/canvas/PaperTabs";
import { Button } from "@/components/ui/button";
import { roveTabs } from "@/lib/paperTabs";
import { cn } from "@/lib/utils";

import { notePanelId, noteTabId } from "./noteTabIds";

export type NoteTab = "paper" | "chat";

interface Props {
  active: NoteTab;
  /** Prefix of the tab and panel ids (the pane's `useId`). */
  idBase: string;
  /** The chat's papers, in the reader's order. */
  papers: PaperTabItem[];
  /** Papers shown but not in the chat (dashed, as in the reader). */
  outside: OutsidePaperTabItem[];
  /** The paper whose note is on the paper tab. */
  paperId: number | null;
  chatTitle: string;
  /** False when the chat has no note worth a tab (it leaves with a motion). */
  chatAvailable: boolean;
  /** The chat tab grows in (a note that just got content), rather than
   *  simply being there when the pane opens. */
  animateChatIn?: boolean;
  onSelectPaper: (paperId: number) => void;
  onAddPaper: (paperId: number) => void;
  onClosePaper: (paperId: number) => void;
  onKeepPaper: (paperId: number) => void;
  onSelectChat: () => void;
  onClose: () => void;
  /** A new value (another chat) redraws the paper tabs without motion. */
  resetKey?: unknown;
}

/** How long the chat tab takes to leave (its exit animation). */
const CHAT_TAB_EXIT_MS = 200;

export function NotesTabs({
  active,
  idBase,
  papers,
  outside,
  paperId,
  chatTitle,
  chatAvailable,
  animateChatIn = false,
  onSelectPaper,
  onAddPaper,
  onClosePaper,
  onKeepPaper,
  onSelectChat,
  onClose,
  resetKey,
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

  return (
    <div className="flex shrink-0 items-center gap-1 border-b border-border px-2 py-1">
      <div
        role="tablist"
        aria-label={t("pane.label", "Notes")}
        className="paper-tabs-scroll flex min-w-0 flex-1 items-center gap-1 overflow-x-auto overflow-y-hidden"
        onKeyDown={roveTabs}
      >
        <PaperTabs
          papers={papers}
          outside={outside}
          selected={active === "paper" ? paperId : null}
          onSelect={onSelectPaper}
          onAdd={onAddPaper}
          onClose={onClosePaper}
          onKeep={onKeepPaper}
          tabTitle={(p) => t("pane.paperTitle", "Notes on: {{title}}", { title: p.title })}
          tabId={(id) => noteTabId(idBase, `paper-${id}`)}
          panelId={notePanelId(idBase)}
          resetKey={resetKey}
          tabStop={!(active === "chat" && chatAvailable)}
        />
        {chatMounted && (
          <button
            type="button"
            role="tab"
            data-note-tab="chat"
            id={noteTabId(idBase, "chat")}
            aria-controls={notePanelId(idBase)}
            aria-selected={active === "chat"}
            tabIndex={active === "chat" && chatAvailable ? 0 : -1}
            aria-hidden={chatAvailable ? undefined : true}
            disabled={!chatAvailable}
            onClick={onSelectChat}
            title={t("pane.chatTitle", "Notes for this chat only: {{title}}", { title: chatTitle })}
            className={cn(
              "inline-flex h-6 min-w-0 max-w-60 shrink-0 items-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors duration-150 motion-reduce:transition-none",
              active === "chat"
                ? "bg-foreground/10 text-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
              "fill-mode-forwards motion-reduce:animate-none",
              chatAvailable
                ? animateChatIn && "animate-in fade-in-0 slide-in-from-right-2 duration-200"
                : "pointer-events-none animate-out fade-out-0 slide-out-to-right-2 duration-200",
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
