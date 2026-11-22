/** The timing of folds (FoldControls.tsx): the toggle's turn and the
 * chip's fade (the fold keys: foldKeys.ts). */
import { useEffect, useState } from "react";

import { prefersReducedMotion } from "@/lib/motion";

/** How long a fold's motion takes (the toggle's turn, the chip's fade). */
export const FOLD_MS = 200;

/** `open` with an exit: mounted until its closing motion has played. */
export function usePresence(open: boolean, ms = FOLD_MS): { mounted: boolean; state: "open" | "closed" } {
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);
  useEffect(() => {
    if (open || !mounted) return;
    const timer = setTimeout(() => setMounted(false), prefersReducedMotion() ? 0 : ms);
    return () => clearTimeout(timer);
  }, [open, mounted, ms]);
  return { mounted: mounted || open, state: open ? "open" : "closed" };
}
