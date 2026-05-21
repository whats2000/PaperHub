import { Loader2 } from "lucide-react";

// Varied line widths so the skeleton reads like a page of prose, not a grid.
const LINE_WIDTHS = [
  "w-full",
  "w-11/12",
  "w-10/12",
  "w-full",
  "w-9/12",
  "w-11/12",
  "w-8/12",
  "w-10/12",
  "w-9/12",
];

interface Props {
  /** Status text under the skeleton. */
  label?: string;
}

/**
 * Loading placeholder for the reading panel: a pulsing document skeleton (title,
 * byline, a paragraph of lines) plus a spinner + label. Used both for the brief
 * swap interlude (DeferredRemount) and while react-pdf parses a PDF, so every
 * "paper is loading" moment looks the same.
 */
export function PaperLoading({ label = "Loading paper…" }: Props) {
  return (
    <div className="flex h-full w-full flex-1 flex-col items-center justify-center gap-5 overflow-hidden p-6">
      <div className="w-full max-w-md space-y-3 rounded-lg border border-border bg-background/50 p-6 shadow-sm">
        {/* title + byline */}
        <div className="h-5 w-3/4 animate-pulse rounded bg-muted" />
        <div className="h-3 w-2/5 animate-pulse rounded bg-muted/70" />
        {/* paragraph */}
        <div className="space-y-2.5 pt-4">
          {LINE_WIDTHS.map((w, i) => (
            <div
              key={i}
              className={`h-2.5 animate-pulse rounded bg-muted/60 ${w}`}
            />
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span>{label}</span>
      </div>
    </div>
  );
}
