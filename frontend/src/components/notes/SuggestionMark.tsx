/** The bar under one of the agent's suggestions on the user's text: "Suggested
 * by the assistant" with Accept and Reject. The change itself (the user's
 * words struck through, the proposal inserted) is drawn in the text by the
 * suggestion decorations (noteSuggestions.tsx); on a decision the bar and
 * those marks fade out together, then the note shows the saved text. */
import { Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

/** How long the change takes to fade out (matches duration-200). */
export const SUGGESTION_EXIT_MS = 200;

export interface SuggestionMarkProps {
  suggestionId: string;
  /** A suggested delete (the whole line goes). */
  deletes: boolean;
  /** The proposal, shown here when the block has no text to mark it in. */
  detached: string | null;
  /** Posts the decision and shows the saved note, calling `exit` (the
   * change animating out) in between. Rejects when the decision failed. */
  onDecide: (accept: boolean, exit: () => Promise<void>) => Promise<void>;
}

/** The marks of this suggestion in the text fade with the bar. */
function markLeaving(id: string, on: boolean) {
  document.querySelectorAll(`[data-suggestion-id="${CSS.escape(id)}"]`).forEach((el) => {
    if (on) el.setAttribute("data-leaving", "");
    else el.removeAttribute("data-leaving");
  });
}

export function SuggestionMark({ suggestionId, deletes, detached, onDecide }: SuggestionMarkProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<"accept" | "reject" | null>(null);
  const [leaving, setLeaving] = useState(false);

  const decide = async (accept: boolean) => {
    if (busy) return;
    setBusy(accept ? "accept" : "reject");
    const exit = () =>
      new Promise<void>((done) => {
        setLeaving(true);
        markLeaving(suggestionId, true);
        setTimeout(done, prefersReducedMotion() ? 0 : SUGGESTION_EXIT_MS);
      });
    try {
      await onDecide(accept, exit);
    } catch {
      // Still on screen (the decision failed): show it again.
      setBusy(null);
      setLeaving(false);
      markLeaving(suggestionId, false);
    }
  };

  return (
    <span
      data-testid="suggestion-mark"
      data-suggestion-id={suggestionId}
      data-closed={leaving ? "" : undefined}
      className={cn(
        "note-sug-bar mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-amber-700 dark:text-amber-300",
        "origin-top transition-[opacity,scale] duration-200 ease-out data-closed:scale-y-95 data-closed:opacity-0",
        "animate-in fade-in-0 motion-reduce:animate-none motion-reduce:transition-none",
      )}
    >
      {detached !== null && (
        <ins className="note-sug-ins basis-full" data-suggestion-id={suggestionId}>
          {detached}
        </ins>
      )}
      <Sparkles className="size-3 shrink-0" aria-hidden />
      <span>{deletes ? t("notes:suggestion.deleteLine", "Suggested removing this line") : t("notes:suggestion.by", "Suggested by the assistant")}</span>
      <span className="inline-flex gap-1">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void decide(true)}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 text-[11.5px] text-foreground transition-colors hover:bg-accent disabled:opacity-50"
        >
          {busy === "accept" && <Loader2 className="size-3 animate-spin" aria-hidden />}
          {t("notes:suggestion.accept", "Accept")}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void decide(false)}
          className="inline-flex items-center gap-1 rounded-md px-2 text-[11.5px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        >
          {busy === "reject" && <Loader2 className="size-3 animate-spin" aria-hidden />}
          {t("notes:suggestion.reject", "Reject")}
        </button>
      </span>
    </span>
  );
}
