import { useMemo } from "react";
import { Presentation } from "lucide-react";

import type { ToolCallRecord } from "@/types/domain";

/**
 * Maps the most-recent `report:*` trace step to a human, present-tense status
 * so the card reflects what the slide agent is doing right now — even during
 * the long, silent stretches (e.g. a multi-second pdflatex compile) where no
 * new step closes for a while. The backend streams each fan-out step live
 * (see report_graph `_then_flush`), so the tail advances as work progresses.
 */
function stageLabel(trace: ToolCallRecord[] | undefined): string {
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

/**
 * In-flight indicator for the `slides` intent. Renders between the routing
 * badge and the (collapsed) trace panel while the assistant turn is still
 * streaming and before the `deck` event lands, then unmounts. Purely a status
 * affordance — sets expectations ("can take a few minutes") so the dead air
 * during a draft fan-out or a pdflatex compile doesn't read as a stall.
 */
export function SlideProgressCard({ trace }: { trace?: ToolCallRecord[] }) {
  const stage = useMemo(() => stageLabel(trace), [trace]);
  const steps = trace?.length ?? 0;
  // During the draft fan-out, count drafted frames — it's the most legible
  // progress signal (one step per slide). Otherwise show the total step count.
  const draftCount = useMemo(
    () => (trace ?? []).filter((r) => r.tool.toLowerCase().includes("draft")).length,
    [trace],
  );
  const inDraft = (trace?.[trace.length - 1]?.tool ?? "").toLowerCase().includes("draft");

  return (
    <div
      role="status"
      aria-live="polite"
      className="relative overflow-hidden rounded-xl border border-border bg-card/70"
    >
      <div className="flex items-center gap-3 p-3">
        {/* Slide-deck build motif: a stack of frames; the top frame's title bar,
            bullet lines and figure box pulse while a beam sweeps across it. */}
        <div aria-hidden className="relative h-12 w-14 shrink-0">
          {/* Back frames of the stack (offset, faded) for depth. */}
          <div className="absolute left-1.5 top-1.5 h-10 w-11 rounded-md border border-border bg-muted/30" />
          <div className="absolute left-0.5 top-0.5 h-10 w-11 rounded-md border border-border bg-muted/40" />
          {/* Front frame — the one being built. */}
          <div className="absolute inset-0 h-10 w-11 overflow-hidden rounded-md border border-border bg-muted/50">
            {/* Title bar. */}
            <span className="absolute left-1 top-1 h-1 w-7 rounded-full bg-foreground/40 motion-safe:animate-slide-build-line" />
            {/* Bullet lines. */}
            <span className="absolute left-1 top-3.5 h-[3px] w-5 rounded-full bg-foreground/30 motion-safe:animate-slide-build-line [animation-delay:200ms]" />
            <span className="absolute left-1 top-5 h-[3px] w-4 rounded-full bg-foreground/30 motion-safe:animate-slide-build-line [animation-delay:400ms]" />
            {/* Figure box. */}
            <span className="absolute bottom-1 right-1 h-3 w-3 rounded-[2px] border border-foreground/30 bg-foreground/10 motion-safe:animate-slide-build-line [animation-delay:600ms]" />
            {/* Beam sweeping across the frame. */}
            <div className="pointer-events-none absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-foreground/25 to-transparent motion-safe:animate-slide-build-beam" />
          </div>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Presentation className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">
              Building your slide deck
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{stage}</p>
          <p className="mt-1 text-[11px] leading-tight text-muted-foreground/70">
            Building slides — drafting and compiling can take a few minutes.
            {inDraft && draftCount > 0 ? (
              <>
                {" · "}
                {draftCount} slide{draftCount === 1 ? "" : "s"} drafted so far
              </>
            ) : (
              steps > 0 && (
                <>
                  {" · "}
                  {steps} step{steps === 1 ? "" : "s"} so far
                </>
              )
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
