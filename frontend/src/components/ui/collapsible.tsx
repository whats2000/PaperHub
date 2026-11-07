import { useEffect, useRef, type ReactNode } from "react";

import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

// Matches duration-200 plus a margin: fallback if no transitionend arrives.
const EXPAND_FALLBACK_MS = 250;

/**
 * Height-animated inline expander (grid 0fr -> 1fr). Closed content is inert
 * and hidden from assistive tech. `onExpanded` fires exactly once per open
 * (on transitionend, a fallback timer, or immediately under reduced motion)
 * so callers can scroll the content in. `className` lands on the grid root, so
 * vertical padding and borders belong on the children (they don't collapse at
 * 0fr). The inner `overflow-hidden` clips even when open: focus rings and
 * absolutely positioned children get cut off, so portal any popover.
 */
export function Collapsible({
  open,
  children,
  className,
  onExpanded,
}: {
  open: boolean;
  children: ReactNode;
  className?: string;
  onExpanded?: (el: HTMLDivElement) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(open);
  const fired = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cb = useRef(onExpanded);
  useEffect(() => {
    cb.current = onExpanded;
  });

  const clearTimer = () => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  };
  const fire = () => {
    clearTimer();
    if (fired.current || !ref.current) return;
    fired.current = true;
    cb.current?.(ref.current);
  };

  useEffect(() => {
    if (open && !wasOpen.current) {
      fired.current = false;
      if (prefersReducedMotion()) fire();
      else timer.current = setTimeout(fire, EXPAND_FALLBACK_MS);
    } else if (!open) {
      fired.current = true;
      clearTimer();
    }
    wasOpen.current = open;
    return clearTimer;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <div
      ref={ref}
      data-state={open ? "open" : "closed"}
      inert={!open}
      aria-hidden={open ? undefined : true}
      onTransitionEnd={(e) => {
        if (
          open &&
          e.target === e.currentTarget &&
          e.propertyName === "grid-template-rows"
        ) {
          fire();
        }
      }}
      className={cn(
        "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
        open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        className,
      )}
    >
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  );
}
