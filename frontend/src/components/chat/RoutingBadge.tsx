import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { RoutingDecision } from "@/types/domain";

export function RoutingBadge({ decision }: { decision: RoutingDecision }) {
  const { t } = useTranslation("chat");
  const conf = decision.confidence;
  const confLevel = conf >= 0.8 ? "high" : conf >= 0.5 ? "mid" : "low";
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              data-conf={confLevel}
              className="inline-flex items-center gap-2 text-xs cursor-default focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
            />
          }
        >
          <Badge variant={confLevel === "low" ? "destructive" : "secondary"}>
            {t(`routing.${decision.intent}`)}
          </Badge>
          <span className="text-muted-foreground">
            {Math.round(conf * 100)}% · {decision.model_tier}
          </span>
        </TooltipTrigger>
        <TooltipContent>
          <p className="max-w-xs text-sm">{decision.reasoning}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
