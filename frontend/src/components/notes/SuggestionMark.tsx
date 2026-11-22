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
  /** A table's caption / notes the proposal changes (they are props, with
   * no text in the note to mark): shown here, old struck, new inserted. */
  partChanges?: readonly TablePartChange[];
  /** The cells a table proposal changes (the cells are not shown whole). */
  cellChanges?: readonly TableCellChange[];
  /** Posts the decision and shows the saved note, calling `exit` (the
   * change animating out) in between. Rejects when the decision failed;
   * resolves "kept" when the suggestion stays open (shown again). */
  onDecide: (accept: boolean, exit: () => Promise<void>) => Promise<void | "kept">;
}

/** The marks of this suggestion in the text fade with the bar. */
function markLeaving(id: string, on: boolean) {
  document.querySelectorAll(`[data-suggestion-id="${CSS.escape(id)}"]`).forEach((el) => {
    if (on) el.setAttribute("data-leaving", "");
    else el.removeAttribute("data-leaving");
  });
}

export interface TablePartChange {
  part: "caption" | "notes";
  from: string;
  to: string;
}

/** One change a table suggestion makes to the cells: a cell (named by its
 * row's first cell and its column's header), or a whole row added or
 * removed. `rowN` / `colN` are 1-based (0: not a column); a row or column
 * without a name is shown by its number. */
export interface TableCellChange {
  kind: "cell" | "addRow" | "removeRow";
  row: string;
  rowN: number;
  column: string;
  colN: number;
  from: string;
  to: string;
}

export function SuggestionMark({
  suggestionId,
  deletes,
  detached,
  partChanges = [],
  cellChanges = [],
  onDecide,
}: SuggestionMarkProps) {
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
    const again = () => {
      setBusy(null);
      setLeaving(false);
      markLeaving(suggestionId, false);
    };
    try {
      // Kept open (its text changed meanwhile): usable again.
      if ((await onDecide(accept, exit)) === "kept") again();
    } catch {
      // Still on screen (the decision failed): show it again.
      again();
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
      {partChanges.map((c) => (
        <span
          key={c.part}
          data-testid={`suggestion-part-${c.part}`}
          className="basis-full whitespace-pre-wrap text-foreground"
        >
          <span className="font-medium">
            {c.part === "caption"
              ? t("notes:table.captionField", "Table caption")
              : t("notes:table.notesField", "Table notes")}
            {": "}
          </span>
          {c.from && (
            <del className="note-sug-del" data-suggestion-id={suggestionId}>
              {c.from}
            </del>
          )}
          {c.from && c.to && " "}
          {c.to && (
            <ins className="note-sug-ins" data-suggestion-id={suggestionId}>
              {c.to}
            </ins>
          )}
        </span>
      ))}
      {cellChanges.map((c, i) => {
        // The first cell names its row, so a change to it names the row by number.
        const row = c.colN !== 1 && c.row ? c.row : t("notes:suggestion.rowN", "Row {{n}}", { n: c.rowN });
        const column = c.column || t("notes:suggestion.columnN", "Column {{n}}", { n: c.colN });
        const label =
          c.kind === "addRow"
            ? t("notes:suggestion.addRow", "New row")
            : c.kind === "removeRow"
              ? t("notes:suggestion.removeRow", "Row removed")
              : t("notes:suggestion.cell", "{{row}} · {{column}}", { row, column });
        return (
          <span key={i} data-testid="suggestion-cell" className="basis-full whitespace-pre-wrap text-foreground">
            <span className="font-medium">
              {label}
              {": "}
            </span>
            {c.from && (
              <del className="note-sug-del" data-suggestion-id={suggestionId}>
                {c.from}
              </del>
            )}
            {c.from && c.to && " "}
            {c.to && (
              <ins className="note-sug-ins" data-suggestion-id={suggestionId}>
                {c.to}
              </ins>
            )}
          </span>
        );
      })}
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
