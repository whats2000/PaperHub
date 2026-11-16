/** The one-time editor tip: shown until dismissed, then remembered. */
import { X } from "lucide-react";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

export const TIP_STORAGE_KEY = "paperhub-notes-tip-v1";

function seen(): boolean {
  try {
    return localStorage.getItem(TIP_STORAGE_KEY) !== null;
  } catch {
    return false; // storage blocked: show the tip
  }
}

export function EditorTip({ onOpenGuide }: { onOpenGuide: () => void }) {
  const { t } = useTranslation();
  // Dismissed in an earlier visit: not rendered at all. Dismissed now: it
  // closes by collapsing, so the note below glides up instead of jumping.
  const [gone] = useState(seen);
  const [open, setOpen] = useState(true);
  if (gone) return null;

  const dismiss = () => {
    setOpen(false);
    try {
      localStorage.setItem(TIP_STORAGE_KEY, "1");
    } catch {
      /* storage blocked: it returns next time */
    }
  };
  const kbd = <kbd className="rounded border border-border bg-background px-1 font-mono text-[11px] text-foreground" />;

  return (
    <div
      data-state={open ? "open" : "closed"}
      inert={!open}
      className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
    >
      <div className="min-h-0 overflow-hidden">
        <div className="mx-2.5 mt-1.5 flex items-center gap-2 rounded-lg bg-accent py-1.5 pr-1 pl-2.5 text-xs text-muted-foreground">
          <span>
            <Trans
              i18nKey="notes:tip.body"
              defaults="New here? Type <slash>/</slash> for blocks, <bold>**bold**</bold> as you type, or select text for the toolbar."
              components={{ slash: kbd, bold: kbd }}
            />
          </span>
          <button
            type="button"
            className="whitespace-nowrap text-xs text-foreground underline"
            onClick={onOpenGuide}
          >
            {t("notes:tip.all", "All shortcuts")}
          </button>
          <button
            type="button"
            aria-label={t("notes:tip.dismiss", "Dismiss the tip")}
            className="ml-auto inline-flex size-[22px] shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-background hover:text-foreground"
            onClick={dismiss}
          >
            <X className="size-3.5" aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}
