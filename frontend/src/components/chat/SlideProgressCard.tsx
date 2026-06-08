import { useMemo } from "react";
import { Presentation } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ToolCallRecord } from "@/types/domain";
import { slideStageLabel } from "@/lib/slideStage";

/**
 * In-flight indicator for the `slides` intent. Renders between the routing
 * badge and the (collapsed) trace panel while the assistant turn is still
 * streaming and before the `deck` event lands, then unmounts. Purely a status
 * affordance — sets expectations ("can take a few minutes") so the dead air
 * during a draft fan-out or a pdflatex compile doesn't read as a stall.
 */
export function SlideProgressCard({ trace }: { trace?: ToolCallRecord[] }) {
  const { t } = useTranslation("chat");
  const stage = useMemo(() => slideStageLabel(trace), [trace]);
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
              {t("slideCard.title")}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{stage}</p>
          <p className="mt-1 text-[11px] leading-tight text-muted-foreground/70">
            {t("slideCard.hint")}
            {inDraft && draftCount > 0 ? (
              <>
                {" · "}
                {t("slideCard.drafted", { count: draftCount })}
              </>
            ) : (
              steps > 0 && (
                <>
                  {" · "}
                  {t("slideCard.step", { count: steps })}
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
