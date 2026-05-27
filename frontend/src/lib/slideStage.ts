import type { ToolCallRecord } from "@/types/domain";

/**
 * Maps the most-recent `report:*` trace step to a human, present-tense status
 * for the slide flow. Used by both the in-chat SlideProgressCard and the
 * Slides-panel editing mask so they show the same live stage.
 *
 * The backend streams each fan-out step live (see report_graph `_then_flush`),
 * so the tail advances as work progresses through the pipeline.
 */
export function slideStageLabel(trace: ToolCallRecord[] | undefined): string {
  const last = trace && trace.length > 0 ? trace[trace.length - 1] : undefined;
  if (!last) return "Warming up the slide agent…";
  const t = last.tool.toLowerCase();
  if (t.includes("resolve")) return "Gathering the papers…";
  if (t.includes("understand")) return "Studying the papers…";
  if (t.includes("narrate")) return "Outlining the talk…";
  if (t.includes("draft")) return "Drafting slide frames…";
  if (t.includes("coherence")) return "Smoothing the flow…";
  if (t.includes("assemble") || t.includes("verify")) return "Placing figures…";
  if (t.includes("compile")) return "Compiling the deck (LaTeX)…";
  if (t.includes("notes")) return "Finalizing…";
  return "Building the deck…";
}
