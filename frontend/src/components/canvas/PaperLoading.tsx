import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

// Line widths per paragraph; each ends on a short line, like prose.
const PARAGRAPHS = [
  ["w-full", "w-full", "w-11/12", "w-full", "w-7/12"],
  ["w-full", "w-11/12", "w-full", "w-10/12", "w-full", "w-5/12"],
  ["w-full", "w-full", "w-10/12", "w-8/12"],
];

interface Props {
  /** Status text for screen readers. */
  label?: string;
  className?: string;
}

function Bar({ className }: { className: string }) {
  return <div aria-hidden="true" className={cn("skeleton-shimmer rounded-sm", className)} />;
}

/**
 * Loading placeholder for the reading panel, shaped like the page it stands in
 * for: a title, a byline and paragraphs of shimmering bars (Ant Design
 * Skeleton "active" style). Used while a paper is fetched, while an HTML paper
 * lays out, while react-pdf parses a PDF and during a paper swap, so every
 * "paper is loading" moment looks the same.
 */
export function PaperLoading({ label, className }: Props) {
  const { t } = useTranslation("canvas");
  const text = label ?? t("loading.paper");
  return (
    <div
      role="status"
      className={cn("flex h-full w-full flex-1 justify-center overflow-hidden px-8 pt-10", className)}
    >
      <span className="sr-only">{text}</span>
      <div className="w-full max-w-2xl">
        <Bar className="h-7 w-3/5" />
        <Bar className="mt-4 h-4 w-2/5" />
        {PARAGRAPHS.map((lines, p) => (
          <div key={p} className="mt-8 space-y-3">
            {lines.map((w, i) => (
              <Bar key={i} className={cn("h-4", w)} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
