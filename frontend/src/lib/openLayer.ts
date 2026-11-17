/** Layers an Escape closes first (menus, listboxes, dialogs, the editor's
 * suggestion menu), before any mode it would also stop (Focus, Box). */
export const OPEN_LAYER = ['[role="menu"]', '[role="listbox"]', '[role="dialog"]', ".bn-suggestion-menu"]
  .map((s) => `${s}:not([data-closed]):not([hidden])`)
  .join(", ");

export function hasOpenLayer(doc: Document = document): boolean {
  return doc.querySelector(OPEN_LAYER) !== null;
}
