import { useCallback, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useCanvasStore, CANVAS_MIN_WIDTH } from "@/store/canvas";

/** Max canvas width as a fraction of the viewport. */
const MAX_VW = 0.8;

interface CanvasResize {
  width: number;
  /** True while a drag is in progress (consumers disable the iframe's pointer
   *  events + the column-width transition so the drag tracks the cursor). */
  resizing: boolean;
  onPointerDown: (e: ReactPointerEvent) => void;
}

/**
 * Drag-to-resize for the Citation Canvas. The handle sits on the panel's left
 * edge; dragging left widens it. Width is clamped to
 * [CANVAS_MIN_WIDTH, 80vw] and persisted in the canvas store.
 */
export function useCanvasResize(): CanvasResize {
  const width = useCanvasStore((s) => s.width);
  const setWidth = useCanvasStore((s) => s.setWidth);
  const [resizing, setResizing] = useState(false);

  const onPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = useCanvasStore.getState().width;
      setResizing(true);

      const onMove = (ev: PointerEvent | MouseEvent) => {
        const dx = startX - ev.clientX; // drag left → wider
        const max = Math.round(window.innerWidth * MAX_VW);
        const next = Math.min(Math.max(startW + dx, CANVAS_MIN_WIDTH), max);
        setWidth(next);
      };
      const onUp = () => {
        setResizing(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [setWidth],
  );

  return { width, resizing, onPointerDown };
}
