/** "Show my marks" in the reader bar: hides every mark on the paper without
 * deleting any (remembered across sessions). */
import { Eye } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { useMarksStore } from "@/store/marks";

export function ShowMarksToggle({ shown }: { shown: boolean }) {
  const { t } = useTranslation("notes");
  const on = useMarksStore((s) => s.showMarks);
  const setOn = useMarksStore((s) => s.setShowMarks);
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-hidden={!shown}
      inert={!shown ? true : undefined}
      title={
        on
          ? t("marks.hideMarksHint", "Hide your marks on the paper (nothing is deleted)")
          : t("marks.showMarksHint", "Show your marks on the paper")
      }
      onClick={() => setOn(!on)}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-[color,background-color,opacity] duration-150 motion-reduce:transition-none",
        on ? "bg-foreground/10 text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
        !shown && "opacity-0",
      )}
    >
      <Eye aria-hidden className="size-3.5" />
      {/* A narrow reader bar shows the icon only (named by the title). */}
      <span className="@max-[32rem]/readerbar:sr-only">{t("marks.showMarks", "Show my marks")}</span>
    </button>
  );
}
