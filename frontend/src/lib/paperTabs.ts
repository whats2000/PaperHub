/** What the reader and the notes pane share about their paper tabs: the
 * order (the chat's papers, then the dashed ones), where closing a tab
 * lands, and Add (bringing a dashed tab's paper into the chat). */
import type { KeyboardEvent } from "react";
import { toast } from "sonner";

import i18n from "@/lib/i18n";
import { attachFromLibrary, listSessionReferences, toggleReference } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import type { ReferenceItem } from "@/types/domain";

/** The paper a pane lands on when `id`'s tab closes: the next tab, else
 * the one before, else none. */
export function neighbourOf(order: number[], id: number): number | null {
  const at = order.indexOf(id);
  if (at < 0) return null;
  return order[at + 1] ?? order[at - 1] ?? null;
}

/** Adds a paper to the chat: a switched-off paper of the chat is switched
 * back on (optimistic, rolled back on failure); a library paper is
 * attached. False when it failed (the user was told). */
export async function addPaperToChat(backendSessionId: number, paperId: number): Promise<boolean> {
  const chat = useChatStore.getState();
  const known: ReferenceItem | undefined = chat.referencesBySession[backendSessionId]?.find(
    (r) => r.paper_content_id === paperId,
  );
  const failed = () => {
    toast.error(i18n.t("canvas:toast.addPaperFailed"));
    return false;
  };
  if (known === undefined) {
    try {
      await attachFromLibrary(backendSessionId, paperId);
      useChatStore.getState().setReferences(backendSessionId, await listSessionReferences(backendSessionId));
      return true;
    } catch {
      return failed();
    }
  }
  if (known.enabled) return true;
  chat.patchReferenceEnabled(backendSessionId, known.papers_id, true);
  try {
    await toggleReference(known.papers_id, true);
    return true;
  } catch {
    useChatStore.getState().patchReferenceEnabled(backendSessionId, known.papers_id, false);
    return failed();
  }
}

/** Arrow keys on a tablist: the next or previous tab is focused and picked. */
export function roveTabs(e: KeyboardEvent<HTMLElement>): void {
  const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
  if (!step) return;
  const tabs = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]:not([disabled])')].filter(
    (t) => !t.closest("[data-leaving]"),
  );
  const at = tabs.indexOf(e.target as HTMLElement);
  if (at < 0 || tabs.length < 2) return;
  e.preventDefault();
  const next = tabs[(at + step + tabs.length) % tabs.length];
  next?.click();
  next?.focus();
}
