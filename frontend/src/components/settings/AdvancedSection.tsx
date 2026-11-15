import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { Collapsible } from "@/components/ui/collapsible";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

/**
 * A settings group of advanced fields, closed by default. The header's chevron
 * rotates and the body opens downward from it (and closes back into it) with
 * the shared height animation; an opened body scrolls into view. Replaces a
 * native `<details>`, which opens and shuts instantly.
 */
export function AdvancedSection({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  return (
    <div className="mt-2 rounded-md border border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 rounded-md px-3 py-2 text-left text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "size-4 shrink-0 transition-transform duration-200 ease-out motion-reduce:transition-none",
            !open && "-rotate-90",
          )}
        />
        {title}
      </button>
      <div id={bodyId} data-testid="advanced-section-body" className="contents">
        <Collapsible
          open={open}
          onExpanded={(el) =>
            el.scrollIntoView({
              block: "nearest",
              behavior: prefersReducedMotion() ? "auto" : "smooth",
            })
          }
        >
          <div className="border-t border-border p-3 pb-0">{children}</div>
        </Collapsible>
      </div>
    </div>
  );
}
