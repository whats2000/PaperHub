import { prefersReducedMotion } from "@/lib/motion";
import { useEffect, useRef, useState } from "react";

/**
 * Keeps an element mounted for `exitMs` after `open` turns false so its exit
 * animation can play. Render `data-state={state}` on the element, e.g.:
 *
 *   {mounted && <div data-state={state} className="duration-150
 *     data-[state=open]:animate-in data-[state=open]:fade-in-0
 *     data-[state=closed]:animate-out data-[state=closed]:fade-out-0
 *     motion-reduce:animate-none" />}
 *
 * `motion-reduce:animate-none` covers the enter; the exit timer already drops
 * to 0 ms under reduced motion.
 */
export function usePresence(
  open: boolean,
  exitMs = 150,
): { mounted: boolean; state: "open" | "closed" } {
  const [mounted, setMounted] = useState(open);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Derive-during-render: opening mounts immediately, no effect round-trip.
  if (open && !mounted) setMounted(true);

  useEffect(() => {
    if (open) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      setMounted(false);
    }, prefersReducedMotion() ? 0 : exitMs);
    return () => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [open, exitMs]);

  return { mounted: open || mounted, state: open ? "open" : "closed" };
}
