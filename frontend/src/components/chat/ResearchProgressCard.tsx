import { useMemo } from "react";
import { Telescope } from "lucide-react";

import type { Intent, ToolCallRecord } from "@/types/domain";

/**
 * Maps the most-recent trace step to a human, present-tense status so the
 * card reflects what the agent is actually doing right now — even during the
 * long, silent stretches (e.g. a multi-minute arXiv full-text fetch) where no
 * new step closes for a while.
 */
function stageLabel(trace: ToolCallRecord[] | undefined): string {
  const last = trace && trace.length > 0 ? trace[trace.length - 1] : undefined;
  if (!last) return "Warming up the research agent…";
  const t = last.tool.toLowerCase();
  if (t.includes("parse")) return "Understanding your request…";
  if (t.includes("discover")) return "Identifying candidate papers…";
  if (t.includes("web") || t.includes("search_web")) return "Searching the web for sources…";
  if (t.includes("resolve") || t.includes("semantic")) return "Matching against Semantic Scholar…";
  if (t.includes("ingest") || t.includes("arxiv") || t.includes("fetch") || t.includes("download"))
    return "Fetching full text from arXiv…";
  if (t.includes("section") || t.includes("read") || t.includes("subagent")) return "Reading the papers…";
  if (t.includes("finalize") || t.includes("synth")) return "Writing up the findings…";
  return "Gathering paper information…";
}

const headingFor: Partial<Record<Intent, string>> = {
  paper_search: "Conducting deep research",
  paper_suggest: "Curating recommendations",
};

/**
 * In-flight indicator for the long-running research intents. Renders between
 * the routing badge and the (collapsed) trace panel while the assistant turn
 * is still streaming, then unmounts when the final message lands. Purely a
 * status affordance — sets expectations ("can take a few minutes") so the
 * dead air between step completions doesn't read as a stall.
 */
export function ResearchProgressCard({
  intent,
  trace,
}: {
  intent: Intent;
  trace?: ToolCallRecord[];
}) {
  const stage = useMemo(() => stageLabel(trace), [trace]);
  const title = headingFor[intent] ?? "Conducting deep research";
  const steps = trace?.length ?? 0;

  return (
    <div
      role="status"
      aria-live="polite"
      className="relative overflow-hidden rounded-xl border border-border bg-card/70"
    >
      <div className="flex items-center gap-3 p-3">
        {/* Scanning-document motif: text lines pulse, a beam sweeps over them. */}
        <div
          aria-hidden
          className="relative h-12 w-10 shrink-0 overflow-hidden rounded-md border border-border bg-muted/40"
        >
          <div className="absolute inset-0 flex flex-col justify-center gap-1 px-1.5">
            <span className="h-[3px] w-full rounded-full bg-foreground/30 motion-safe:animate-research-line" />
            <span className="h-[3px] w-4/5 rounded-full bg-foreground/30 motion-safe:animate-research-line [animation-delay:200ms]" />
            <span className="h-[3px] w-11/12 rounded-full bg-foreground/30 motion-safe:animate-research-line [animation-delay:400ms]" />
            <span className="h-[3px] w-3/5 rounded-full bg-foreground/30 motion-safe:animate-research-line [animation-delay:600ms]" />
          </div>
          <div className="pointer-events-none absolute inset-x-0 top-0 h-1/3 bg-gradient-to-b from-transparent via-foreground/30 to-transparent motion-safe:animate-research-beam" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Telescope className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">{title}</span>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{stage}</p>
          <p className="mt-1 text-[11px] leading-tight text-muted-foreground/70">
            Deep search — gathering and verifying papers can take a few minutes.
            {steps > 0 && (
              <>
                {" · "}
                {steps} step{steps === 1 ? "" : "s"} so far
              </>
            )}
          </p>
        </div>
      </div>

      {/* Indeterminate shimmer — a sweeping highlight along the bottom edge. */}
      <div
        aria-hidden
        className="h-0.5 w-full bg-[length:200%_100%] bg-gradient-to-r from-transparent via-foreground/40 to-transparent motion-safe:animate-research-shimmer"
      />
    </div>
  );
}
