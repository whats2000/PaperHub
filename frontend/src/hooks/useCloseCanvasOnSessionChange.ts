import { useEffect, useRef } from "react";

import { useCanvasStore } from "@/store/canvas";

/**
 * Close the Citation Canvas whenever the active chat session changes.
 *
 * The canvas shows the *active session's* references; leaving it open across a
 * session switch would display the previous session's paper + stale switcher
 * tabs. The previous id is tracked in a ref inside the effect (ref access in
 * effects is allowed) so we close only on a real change, not on initial mount.
 * `closeCanvas` is a zustand action (not a React `useState` setter), so calling
 * it here is not a set-state-in-effect cascade.
 */
export function useCloseCanvasOnSessionChange(
  activeSessionId: number | null,
): void {
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const prevSessionRef = useRef(activeSessionId);
  useEffect(() => {
    if (prevSessionRef.current !== activeSessionId) {
      prevSessionRef.current = activeSessionId;
      closeCanvas();
    }
  }, [activeSessionId, closeCanvas]);
}
