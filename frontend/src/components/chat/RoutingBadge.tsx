import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { RoutingDecision } from "@/types/domain";

const intentLabel: Record<RoutingDecision["intent"], string> = {
  paper_search: "Paper search",
  paper_suggest: "Paper recommendations",
  paper_qa: "Paper Q&A",
  slides: "Slides",
  library_stats: "Library stats",
  chitchat: "Chitchat",
};

export function RoutingBadge({ decision }: { decision: RoutingDecision }) {
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
            {intentLabel[decision.intent]}
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
