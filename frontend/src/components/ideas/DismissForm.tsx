import { useState } from "react";
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Collapsible } from "@/components/ui/collapsible";
import { Textarea } from "@/components/ui/textarea";
import { REASON_FALLBACK } from "@/lib/ideaActions";
import { revealInScroll } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { DISMISS_REASONS, type DismissReason } from "@/types/ideation";

/**
 * The reason form under an idea: expands in place (height), scrolls itself
 * into view, and collapses back on cancel. Confirm needs at least one reason.
 */
export function DismissForm({
  open,
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (reasons: DismissReason[], note: string) => void;
}) {
  const { t } = useTranslation("ideas");
  const [picked, setPicked] = useState<DismissReason[]>([]);
  const [note, setNote] = useState("");
  // Each opening starts blank (reset while closed, so the collapse still shows the old content).
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setPicked([]);
      setNote("");
    }
  }

  const toggle = (r: DismissReason) =>
    setPicked((p) => (p.includes(r) ? p.filter((x) => x !== r) : [...p, r]));

  return (
    <Collapsible open={open} onExpanded={(el) => revealInScroll(el, "end")}>
      <div data-testid="dismiss-form" className="pt-2.5">
        <div className="rounded-lg border bg-popover p-3 text-popover-foreground">
          <div id="dismiss-title" className="mb-2 text-[13px] font-semibold">
            {t("dismiss.title", "Why doesn't it fit?")}
          </div>
          <div role="group" aria-labelledby="dismiss-title" className="flex flex-wrap gap-1.5">
            {DISMISS_REASONS.map((r) => {
              const on = picked.includes(r);
              return (
                <button
                  key={r}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(r)}
                  className={cn(
                    "inline-flex h-[26px] items-center gap-1 rounded-full border border-border px-2.5 text-[12.5px] text-muted-foreground transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
                    on && "border-foreground bg-accent text-foreground",
                  )}
                >
                  {on && <Check className="size-3.5" aria-hidden="true" />}
                  {t(`dismiss.reasons.${r}`, REASON_FALLBACK[r])}
                </button>
              );
            })}
          </div>
          <Textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("dismiss.notePlaceholder", "Add a note (optional)")}
            aria-label={t("dismiss.noteLabel", "Dismissal note")}
            className="mt-2 min-h-0 text-sm"
          />
          <p className="mt-1.5 mb-2 text-xs text-muted-foreground">
            {t("dismiss.hint", "Reasons improve ideation; similar ideas won't be suggested again for this problem.")}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={onCancel}>
              {t("dismiss.cancel", "Cancel")}
            </Button>
            <Button
              size="sm"
              disabled={picked.length === 0 || busy}
              onClick={() => onConfirm(picked, note.trim())}
            >
              {t("dismiss.confirm", "Dismiss idea")}
            </Button>
          </div>
        </div>
      </div>
    </Collapsible>
  );
}
