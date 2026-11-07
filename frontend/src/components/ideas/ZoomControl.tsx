import { Maximize, ZoomIn, ZoomOut } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ZoomControlProps {
  /** Current zoom factor (1 = 100 %). */
  k: number;
  min?: number;
  max?: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  /** The detail drawer is open: slide left by its width so it stays reachable. */
  shifted: boolean;
}

/** The graph's − / % / + / fit control, top-right of the graph. */
export function ZoomControl({ k, min = 0.5, max = 3, onZoomIn, onZoomOut, onFit, shifted }: ZoomControlProps) {
  const { t } = useTranslation("ideas");
  const out = t("ideas:zoom.out", "Zoom out");
  const zin = t("ideas:zoom.in", "Zoom in");
  const fit = t("ideas:zoom.fit", "Fit to view");
  return (
    <div
      data-testid="zoom-control"
      role="toolbar"
      aria-label={t("ideas:zoom.aria", "Zoom")}
      className={cn(
        "absolute top-3 right-3.5 z-10 flex items-center gap-1 rounded-md border bg-background p-[3px] shadow-md",
        "transition-transform duration-[240ms] ease-[cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-none",
        shifted && "-translate-x-[340px]",
      )}
    >
      <Button variant="ghost" size="icon-sm" aria-label={out} title={out} disabled={k <= min + 1e-6} onClick={onZoomOut}>
        <ZoomOut aria-hidden="true" />
      </Button>
      {/* The level is plain visible text, announced politely as it changes (no
          role="status": the page keeps one status region for generation). */}
      <span aria-live="polite" aria-atomic="true" className="min-w-10 text-center text-xs text-muted-foreground tabular-nums">
        {`${Math.round(k * 100)}%`}
      </span>
      <Button variant="ghost" size="icon-sm" aria-label={zin} title={zin} disabled={k >= max - 1e-6} onClick={onZoomIn}>
        <ZoomIn aria-hidden="true" />
      </Button>
      <Button variant="ghost" size="icon-sm" aria-label={fit} title={fit} onClick={onFit}>
        <Maximize aria-hidden="true" />
      </Button>
    </div>
  );
}
