/**
 * Small badge showing the router's intent classification.
 *
 * Phase A: shows intent label only (model_tier is always "flagship" for paper_qa).
 * Phase B: show model_tier + latency when available.
 */

import type { RoutingDecision } from "../../api/types";

interface Props {
  decision: RoutingDecision;
}

const INTENT_LABELS: Record<string, string> = {
  paper_qa: "Paper Q&A",
  chitchat: "Off-topic",
  library_stats: "Library Stats",
  research_suggest: "Research Suggestion",
  slides: "Slides",
  mcp_tool: "Tool Call",
};

const INTENT_COLORS: Record<string, string> = {
  paper_qa: "bg-blue-900 text-blue-200 border-blue-700",
  chitchat: "bg-neutral-800 text-neutral-400 border-neutral-600",
};

export function RoutingBadge({ decision }: Props) {
  const label = INTENT_LABELS[decision.intent] ?? decision.intent;
  const color = INTENT_COLORS[decision.intent] ?? "bg-neutral-800 text-neutral-400 border-neutral-600";

  return (
    <span
      role="status"
      aria-label={`Intent: ${label}`}
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${color}`}
    >
      {label}
    </span>
  );
}
