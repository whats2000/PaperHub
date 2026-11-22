/** Where the selected cells' outline, their ⋮ handle and the cell menu go
 * (TableCellHandle.tsx), as Notion's table does: a 2 px outline around the
 * selected range, a ⋮ pill centred on its right border, half outside, and the
 * menu beside the pill — under (or over) the selection when the pane has no
 * room on its right, never over the selected cells.
 *
 * Pure geometry, in viewport pixels: the caller reads the rects.
 */

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The smallest box holding every rect (the selected cells'). */
export function unionBox(rects: readonly Box[]): Box | null {
  if (!rects.length) return null;
  return rects.reduce<Box>(
    (a, r) => ({
      left: Math.min(a.left, r.left),
      top: Math.min(a.top, r.top),
      right: Math.max(a.right, r.right),
      bottom: Math.max(a.bottom, r.bottom),
    }),
    { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity },
  );
}

/** How much of the outline the table's scroll box hides on each side (a
 * wide table scrolled sideways): the outline is clipped there, not drawn
 * along the box's edge. */
export function outlineClip(sel: Box, box: Box): { left: number; right: number } {
  return { left: Math.max(0, box.left - sel.left), right: Math.max(0, sel.right - box.right) };
}

/** The table's scroll box fades on a side it is clipped (tableMotion.ts,
 * `--note-table-fade-l` / `-r`); the outline is outside that box, so it
 * takes the same fade as a mask, in its own pixels: it then sits under the
 * fade as the cells do. No fade: no mask. */
export function outlineMask(sel: Box, box: Box, fadeL: number, fadeR: number): string {
  if (!fadeL && !fadeR) return "";
  const start = box.left - sel.left;
  const end = box.right - sel.left;
  return `linear-gradient(to right, transparent ${start}px, #000 ${start + fadeL}px, #000 ${end - fadeR}px, transparent ${end}px)`;
}

/** The handle's centre: on the selection's right border, halfway down the
 * part of it in view (`view`: the table's scroll box and the pane). Hidden
 * when that border is scrolled out of the box, or no part is in view. */
export function handleSpot(sel: Box, view: Box): { x: number; y: number; visible: boolean } {
  const top = Math.max(sel.top, view.top);
  const bottom = Math.min(sel.bottom, view.bottom);
  const visible = sel.right >= view.left + 2 && sel.right <= view.right + 1 && bottom > top;
  return { x: sel.right, y: (top + bottom) / 2, visible };
}

/** Gap between the handle and its menu, and from the pane's edge. */
export const MENU_GAP = 6;
export const EDGE = 8;

/** Which side of the handle the cell menu (or the Borders… picker) opens on:
 * right of it when the pane has room; else under the selection, or over it
 * when there is no room under it either. */
export function menuSide(
  handle: Box,
  sel: Box,
  bounds: Box,
  size: { width: number; height: number },
): "right" | "bottom" | "top" {
  if (handle.right + MENU_GAP + size.width <= bounds.right - EDGE) return "right";
  if (sel.bottom + 4 + size.height <= bounds.bottom - EDGE) return "bottom";
  if (sel.top - 4 - size.height >= bounds.top + EDGE) return "top";
  return "bottom";
}

/** The keys that open the cell menu from a cell: Ctrl/⌘ + /, the context
 * menu key, Shift + F10. */
export function isCellMenuKey(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">): boolean {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key === "/") return true;
  if (e.key === "ContextMenu") return true;
  return e.shiftKey && !e.ctrlKey && !e.metaKey && e.key === "F10";
}

/** The chord as shown on this platform. */
export function cellMenuChord(): string {
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  return mac ? "⌘/" : "Ctrl+/";
}

/** Where a menu opening from the handle goes (Base UI's positioner). */
export interface HandlePlacement {
  anchor: Element | { getBoundingClientRect: () => DOMRect; contextElement?: Element };
  side: "right" | "bottom" | "top";
  align: "start" | "end";
  sideOffset: number;
  alignOffset: number;
}

export const MENU_SIZE = { width: 224, height: 380 };
export const PICKER_SIZE = { width: 252, height: 210 };

/** The pane the menu should stay in (the notes scroller), within the window. */
function boundsOf(el: Element): Box {
  const pane = el.closest(".paperhub-notes")?.getBoundingClientRect();
  const w = window.innerWidth || Infinity;
  const h = window.innerHeight || Infinity;
  return {
    left: Math.max(pane?.left ?? 0, 0),
    top: Math.max(pane?.top ?? 0, 0),
    right: Math.min(pane && pane.width ? pane.right : w, w),
    bottom: Math.min(pane && pane.height ? pane.bottom : h, h),
  };
}

/** Beside the handle when the pane has room, else under / over the
 * selection with its right edge at the handle's. */
export function besideHandle(handle: HTMLElement, sel: () => Box | null, size: { width: number; height: number }): HandlePlacement {
  const h = handle.getBoundingClientRect();
  const s = sel() ?? h;
  const side = menuSide(h, s, boundsOf(handle), size);
  if (side === "right") return { anchor: handle, side, align: "start", sideOffset: MENU_GAP, alignOffset: -4 };
  return {
    anchor: {
      contextElement: handle,
      getBoundingClientRect: () => {
        const hr = handle.getBoundingClientRect();
        const sr = sel() ?? hr;
        return new DOMRect(hr.right, sr.top, 0, sr.bottom - sr.top);
      },
    },
    side,
    align: "end",
    sideOffset: 4,
    alignOffset: 0,
  };
}
