import { useCallback, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useCanvasStore, CANVAS_MIN_WIDTH } from "@/store/canvas";
import {
  maxNotesWidth,
  NOTES_MIN_WIDTH,
  SIDE_BY_SIDE_MIN,
  useNotesPaneStore,
} from "@/store/notesPane";

/** Max canvas width as a fraction of the viewport. */
const MAX_VW = 0.8;

interface CanvasResize {
  width: number;
  /** True while a drag is in progress (consumers disable the iframe's pointer
   *  events + the column-width transition so the drag tracks the cursor). */
  resizing: boolean;
  onPointerDown: (e: ReactPointerEvent) => void;
}

/** A drag on an edge to the LEFT of the pane it sizes: dragging left widens
 *  it. `clamp` bounds each new width. */
function useLeftEdgeDrag(
  readWidth: () => number,
  setWidth: (width: number) => void,
  clamp: (width: number) => number,
): Omit<CanvasResize, "width"> {
  const [resizing, setResizing] = useState(false);

  const onPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = readWidth();
      setResizing(true);

      const onMove = (ev: PointerEvent | MouseEvent) => {
        const dx = startX - ev.clientX; // drag left → wider
        setWidth(clamp(startW + dx));
      };
      const onUp = () => {
        setResizing(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [readWidth, setWidth, clamp],
  );

  return { resizing, onPointerDown };
}

const readCanvasWidth = () => useCanvasStore.getState().width;
const clampCanvasWidth = (w: number) =>
  Math.min(Math.max(w, CANVAS_MIN_WIDTH), Math.round(window.innerWidth * MAX_VW));

/**
 * Drag-to-resize for the right panel (the Citation Canvas and the panes
 * sharing it). The handle sits on the panel's left edge; dragging left widens
 * it. Width is clamped to [CANVAS_MIN_WIDTH, 80vw] and persisted in the
 * canvas store. Side by side with Notes, only the reader grows.
 */
export function useCanvasResize(): CanvasResize {
  const width = useCanvasStore((s) => s.width);
  const setWidth = useCanvasStore((s) => s.setWidth);
  // Narrowed from side by side to tabs: the reader being dragged stays shown.
  const setPanelWidth = useCallback(
    (next: number) => {
      const prev = useCanvasStore.getState().width;
      setWidth(next);
      if (prev >= SIDE_BY_SIDE_MIN && next < SIDE_BY_SIDE_MIN) {
        useNotesPaneStore.getState().setActiveTab("reader");
      }
    },
    [setWidth],
  );
  return { width, ...useLeftEdgeDrag(readCanvasWidth, setPanelWidth, clampCanvasWidth) };
}

/**
 * Drag-to-resize for the divider between the reader and Notes side by side:
 * it trades width between them, the panel keeping its width. Notes stays
 * within [NOTES_MIN_WIDTH, maxNotesWidth(panel)].
 */
export function useNotesDividerResize(panelWidth: number): Omit<CanvasResize, "width"> {
  const setNotesWidth = useNotesPaneStore((s) => s.setNotesWidth);
  const clamp = useCallback(
    (w: number) => Math.min(Math.max(w, NOTES_MIN_WIDTH), maxNotesWidth(panelWidth)),
    [panelWidth],
  );
  // Start from the width shown (the stored one may not fit this panel).
  const readWidth = useCallback(
    () => clamp(useNotesPaneStore.getState().notesWidth),
    [clamp],
  );
  return useLeftEdgeDrag(readWidth, setNotesWidth, clamp);
}
