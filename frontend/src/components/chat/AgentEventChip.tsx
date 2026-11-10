import { useState } from "react";
import { BrainCircuit, FilePlus2, Loader2, Undo2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { deleteMemory, toggleReference } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useMemoriesStore } from "@/store/memories";
import type { MemorySavedData, PaperAddedData } from "@/types/domain";

export type ChipEvent =
  | { event: "paper_added"; data: PaperAddedData }
  | { event: "memory_saved"; data: MemorySavedData };

/**
 * A "do, show, undo" action the agent took (H4): a paper it added to this
 * chat or a memory it saved, with Undo (paper: disable the reference;
 * memory: delete it). Fades and zooms in when the tool returns.
 */
export function AgentEventChip({
  ev,
  sessionId,
  onChange,
}: {
  ev: ChipEvent;
  /** Backend session id (memory ownership header, reference store key). */
  sessionId: number | null;
  onChange: (patch: { undone: boolean }) => void;
}) {
  const { t } = useTranslation("chat");
  const [busy, setBusy] = useState(false);
  const isPaper = ev.event === "paper_added";
  const Icon = isPaper ? FilePlus2 : BrainCircuit;
  const label = isPaper ? t("agentEvent.paperAdded") : t("agentEvent.memorySaved");
  const text = ev.event === "paper_added" ? ev.data.title : ev.data.content;
  const undone = ev.data.undone === true;
  const canUndo = !undone && ev.data.undoable !== false;

  const undo = async () => {
    setBusy(true);
    try {
      if (ev.event === "paper_added") {
        await toggleReference(ev.data.papers_id, false);
        if (sessionId !== null) {
          useChatStore
            .getState()
            .patchReferenceEnabled(sessionId, ev.data.papers_id, false);
        }
      } else {
        await deleteMemory(ev.data.memory_id, sessionId);
        void useMemoriesStore
          .getState()
          .fetchMemories(sessionId)
          .catch(() => undefined);
      }
      onChange({ undone: true });
    } catch {
      toast.error(t("agentEvent.undoFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span
      data-event={ev.event}
      data-undone={undone || undefined}
      className="not-prose inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs text-muted-foreground animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none"
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="shrink-0 font-medium text-foreground/80">{label}</span>
      <span
        className={`min-w-0 truncate transition-[text-decoration-color] duration-200 ${
          undone
            ? "line-through decoration-muted-foreground"
            : "line-through decoration-transparent"
        }`}
        title={text}
      >
        {text}
      </span>
      {canUndo && (
        <button
          type="button"
          onClick={() => void undo()}
          disabled={busy}
          className="inline-flex shrink-0 items-center gap-1 rounded-sm font-medium text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <Undo2 className="h-3 w-3" aria-hidden />
          )}
          {t("agentEvent.undo")}
        </button>
      )}
      {undone && (
        <span className="shrink-0 italic animate-in fade-in-0 duration-200 motion-reduce:animate-none">
          {t("agentEvent.undone")}
        </span>
      )}
    </span>
  );
}
