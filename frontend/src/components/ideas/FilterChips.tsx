import { CircleCheck, EyeOff, FlaskConical, Star, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { IDEA_FILTERS, filterCounts, useIdeationStore, type IdeaFilter } from "@/store/ideation";

const ICONS: Partial<Record<IdeaFilter, LucideIcon>> = {
  saved: Star,
  trying: FlaskConical,
  done: CircleCheck,
  dismissed: EyeOff,
};

const FALLBACK: Record<IdeaFilter, string> = {
  active: "In progress",
  saved: "Saved",
  trying: "Trying",
  done: "Has result",
  dismissed: "Dismissed",
};

/** Status filter chips with the count of the current scope's ideas under each. */
export function FilterChips() {
  const { t } = useTranslation("ideas");
  const ideas = useIdeationStore((s) => s.graph?.ideas);
  const filter = useIdeationStore((s) => s.filter);
  const setFilter = useIdeationStore((s) => s.setFilter);
  // While a scope loads, a neutral dash holds each chip's width instead of a false 0.
  const counts = ideas ? filterCounts(ideas) : null;

  return (
    <div
      role="radiogroup"
      aria-label={t("filter.aria", "Filter ideas by status")}
      className="flex flex-wrap gap-1.5 px-5 pt-2"
    >
      {IDEA_FILTERS.map((f) => {
        const Icon = ICONS[f];
        const on = f === filter;
        return (
          <button
            key={f}
            type="button"
            role="radio"
            aria-checked={on}
            data-filter={f}
            onClick={() => setFilter(f)}
            className={cn(
              "inline-flex h-[26px] items-center gap-1.5 rounded-full border border-border px-2.5 text-[12.5px] text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
              on && "border-foreground bg-accent text-foreground",
            )}
          >
            {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
            {t(`filter.${f}`, FALLBACK[f])}
            <span
              className="min-w-[1ch] text-center text-[11px] tabular-nums text-muted-foreground"
              data-testid={`filter-count-${f}`}
            >
              {counts ? counts[f] : "–"}
            </span>
          </button>
        );
      })}
    </div>
  );
}
