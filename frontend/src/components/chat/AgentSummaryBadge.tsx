import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { AgentSummary } from "@/types/domain";

/**
 * The turn summary of an agent turn (H4), in the routing badge's place:
 * `Agent · read 2 papers · 4 steps`. It is the turn's only trace toggle
 * (it replaces the Trace button); the tooltip lists the tools the agent used.
 */
export function AgentSummaryBadge({
  summary,
  open,
  onToggle,
}: {
  summary: AgentSummary;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation("chat");
  const Icon = open ? ChevronDown : ChevronRight;
  const tools =
    summary.tools.length > 0
      ? t("agentSummary.tools", { tools: summary.tools.join(", ") })
      : t("agentSummary.noTools");
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={open}
              className="inline-flex items-center gap-1.5 rounded-sm text-xs text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          }
        >
          <Badge variant="secondary">{t("agentSummary.label")}</Badge>
          <span>
            {t("agentSummary.papers", { count: summary.papers_read })}
            {" · "}
            {t("agentSummary.steps", { count: summary.steps })}
          </span>
          <Icon className="h-3 w-3" aria-hidden />
        </TooltipTrigger>
        <TooltipContent>
          <p className="max-w-xs text-sm">{tools}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
