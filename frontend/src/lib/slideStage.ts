import type { ToolCallRecord } from "@/types/domain";

import i18n from "./i18n";

/**
 * Returns the `slides:stage.*` key for the most-recent `report:*` trace step.
 * Exposed separately from {@link slideStageLabel} so callers that already hold
 * a `t` function (e.g. a component with `useTranslation`) can resolve the key
 * in the active language themselves.
 */
export function slideStageKey(trace: ToolCallRecord[] | undefined): string {
  const last = trace && trace.length > 0 ? trace[trace.length - 1] : undefined;
  if (!last) return "stage.warmup";
  const t = last.tool.toLowerCase();
  if (t.includes("resolve")) return "stage.resolve";
  if (t.includes("reading")) return "stage.reading";
  if (t.includes("planning") || t.includes("outline")) return "stage.planning";
  if (t.includes("understand")) return "stage.understand";
  if (t.includes("narrate")) return "stage.narrate";
  if (t.includes("draft")) return "stage.draft";
  if (t.includes("coherence")) return "stage.coherence";
  if (t.includes("assemble") || t.includes("verify")) return "stage.figures";
  if (t.includes("compil")) return "stage.compile";
  if (t.includes("notes")) return "stage.notes";
  return "stage.building";
}

/**
 * Maps the most-recent `report:*` trace step to a human, present-tense status
 * for the slide flow, resolved in the active UI language. Used by both the
 * in-chat SlideProgressCard and the Slides-panel editing mask so they show the
 * same live stage.
 *
 * This is a plain module (no React context), so it resolves against the shared
 * i18n instance directly rather than a hook-provided `t`. React components that
 * must re-render on a language switch should resolve {@link slideStageKey} via
 * their own `useTranslation("slides")` `t` instead.
 *
 * The backend streams each fan-out step live (see report_graph `_then_flush`),
 * so the tail advances as work progresses through the pipeline.
 */
export function slideStageLabel(trace: ToolCallRecord[] | undefined): string {
  return i18n.t(slideStageKey(trace), { ns: "slides" });
}
