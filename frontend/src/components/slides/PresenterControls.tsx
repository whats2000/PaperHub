import { useEffect, useState, type ReactNode } from "react";
import { Radio, Square } from "lucide-react";

import { Button } from "@/components/ui/button";

interface Props {
  /** Epoch ms when presentation began (store presentStartedAtBySession). */
  startedAt: number;
  currentPage: number;
  numPages: number;
  audienceConnected: boolean;
  onStop: () => void;
  /** A react-pdf <Page> of currentPage+1, supplied by SlidesPanel so this
   *  component stays react-pdf-free (and unit-testable). */
  nextPreview?: ReactNode;
  /** Injectable clock for tests; defaults to Date.now. */
  now?: () => number;
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/** Presenter cockpit strip — rendered at the top of the Slides panel's main
 *  column (inside the <Document>) while presenting. */
export function PresenterControls({
  startedAt,
  currentPage,
  numPages,
  audienceConnected,
  onStop,
  nextPreview,
  now = Date.now,
}: Props) {
  const [elapsed, setElapsed] = useState(() => now() - startedAt);
  useEffect(() => {
    const id = setInterval(() => setElapsed(now() - startedAt), 1000);
    return () => clearInterval(id);
  }, [startedAt, now]);

  // Only show the preview group when there IS a next slide AND a node to render
  // (avoids an orphaned empty thumbnail box if a caller omits the slot).
  const showPreview = currentPage < numPages && Boolean(nextPreview);

  return (
    <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-3 py-1.5 text-xs">
      <span className="font-medium tabular-nums" aria-label="elapsed time">
        {formatElapsed(elapsed)}
      </span>
      <span
        className={
          audienceConnected
            ? "flex items-center gap-1 text-green-600 dark:text-green-400"
            : "flex items-center gap-1 text-muted-foreground"
        }
      >
        <Radio className="h-3 w-3" />
        {audienceConnected ? "audience connected" : "audience window closed"}
      </span>
      {showPreview && (
        <span className="ml-auto flex items-center gap-1 text-muted-foreground">
          next →
          <span className="block w-16 overflow-hidden rounded border border-border">
            {nextPreview}
          </span>
        </span>
      )}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className={showPreview ? "h-6 px-2 gap-1" : "ml-auto h-6 px-2 gap-1"}
        onClick={onStop}
        aria-label="stop presenting"
      >
        <Square className="h-3 w-3" />
        Stop
      </Button>
    </div>
  );
}
