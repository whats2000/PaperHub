import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { TurnPhase } from "@/lib/activityTimeline";
import { cn } from "@/lib/utils";

/**
 * The agent turn's trace toggle (H4; FR-02 activity timeline, H5a): the
 * "Agent" tag and the turn summary ("Worked 10.6 s: read 2 sections, ran 1
 * search", "Paused, waiting for your OK on 2 items", "Stopped…"). The tag
 * turns amber while the turn waits for the user. No hover popup: the open
 * trace already says, in plain words, what the agent did.
 */
export function AgentSummaryBadge({
  label,
  phase = "worked",
  open,
  onToggle,
}: {
  label: string;
  phase?: TurnPhase;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation("chat");
  const Icon = open ? ChevronDown : ChevronRight;
  const body = (
    <>
      <Badge
        variant="secondary"
        className={cn(
          "transition-colors duration-200 motion-reduce:transition-none",
          phase === "paused" &&
            "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200",
        )}
      >
        {t("agentSummary.label")}
      </Badge>
      <span className="min-w-0">{label}</span>
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
    </>
  );
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      data-phase={phase}
      className="inline-flex min-w-0 items-center gap-1.5 rounded-sm text-left text-xs text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {body}
    </button>
  );
}
