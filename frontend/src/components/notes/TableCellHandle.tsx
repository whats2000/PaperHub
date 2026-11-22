/** The selected cells' outline and their ⋮ handle, the way Notion's table
 * does it: a 2 px outline around the selection (a caret's cell or a range),
 * a 12 × 22 blue pill centred on its right border, half outside, opening the
 * cell menu beside it. The pill and the outline fade (the pill also scales)
 * in and out with the selection; the pill follows a table scrolled sideways
 * and hides when the selection's right edge is out of the table's box.
 *
 * The menu: Merge / Split, Borders…, Clear contents (with Undo: the text
 * would be lost), rows above / below, the columns' alignment, delete rows.
 * No colours. It opens right of the pill when the pane has room, else under
 * (or over) the selection, never on it, growing from the pill. Enter or
 * Space on the focused pill, or Ctrl/⌘ + / (also the context menu key and
 * Shift + F10) in a cell, open it from the keyboard: arrows, Home and End
 * move, Esc closes back to the pill. Opened with the mouse, the caret stays
 * in the cell, and Esc leaves it there. A second click on the pill closes it.
 *
 * During a mouse drag in the editor the pill takes no pointer events, so a
 * text drag ending over it is never turned into a cell selection.
 */
import { useBlockNoteEditor, useEditorState } from "@blocknote/react";
import { TableMap } from "@tiptap/pm/tables";
import type { EditorView } from "@tiptap/pm/view";
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  ArrowDown,
  ArrowUp,
  Check,
  Combine,
  Eraser,
  Grid2x2,
  SquareSplitHorizontal,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Popover, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import type { Selection } from "./tableBorders";
import {
  besideHandle,
  cellMenuChord,
  handleSpot,
  isCellMenuKey,
  MENU_SIZE,
  outlineClip,
  PICKER_SIZE,
  unionBox,
  type Box,
  type HandlePlacement,
} from "./cellHandleGeometry";
import type { Alignment } from "./tableKeys";
import {
  alignSelectedColumns,
  canMerge,
  canSplit,
  clearCells,
  deleteSelectedRows,
  filledCells,
  insertRow,
  mergeSelected,
  restoreCells,
  selectedAlignment,
  splitSelected,
  tableSelection,
} from "./tableMerge";

/** The Borders… picker's request: the handle, the table and the selected
 * grid cells, and where it opens. */
export interface HandleBordersRequest {
  anchor: HTMLElement;
  blockId: string;
  selection: Selection;
  placement: HandlePlacement;
}

/** The selected cells' box (viewport pixels), their table's scroll box
 * and their block, or null with no selection in a table. */
function measure(view: EditorView): { sel: Box; wrap: HTMLElement; outer: Element | null } | null {
  const t = tableSelection(view.state);
  if (!t) return null;
  const map = TableMap.get(t.table);
  const rects: Box[] = [];
  let cell: HTMLElement | null = null;
  for (const rel of new Set(map.cellsInRect({ left: t.rect.c0, top: t.rect.r0, right: t.rect.c1 + 1, bottom: t.rect.r1 + 1 }))) {
    const dom = view.nodeDOM(t.tablePos + 1 + rel);
    if (dom instanceof HTMLElement) {
      rects.push(dom.getBoundingClientRect());
      cell = dom;
    }
  }
  const sel = unionBox(rects);
  const wrap = cell?.closest<HTMLElement>(".tableWrapper");
  if (!sel || !wrap) return null;
  return { sel, wrap, outer: cell!.closest(".bn-block-outer") };
}

/** The nearest scrolling ancestor's box (the pane), within the window. */
function viewport(el: Element): Box {
  let box: Box = { left: -Infinity, top: 0, right: Infinity, bottom: window.innerHeight || Infinity };
  for (let p = el.parentElement; p; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY;
    if (oy === "auto" || oy === "scroll") {
      const r = p.getBoundingClientRect();
      if (r.height) box = { ...box, top: Math.max(box.top, r.top), bottom: Math.min(box.bottom, r.bottom) };
      break;
    }
  }
  return box;
}

function setOn(el: HTMLElement, on: boolean) {
  if (el.hasAttribute("data-on") !== on) el.toggleAttribute("data-on", on);
}

type After = "editor" | "handle" | "stay";

type Command = "merge" | "split" | "borders" | "clear" | "above" | "below" | "align-left" | "align-center" | "align-right" | "delete";

interface Entry {
  key: Command;
  icon: ReactNode;
  label: ReactNode;
  disabled?: boolean;
  checked?: boolean;
}

const ICON = "size-4 shrink-0";

export function TableCellHandle({
  onBorders,
  pinned,
}: {
  onBorders: (req: HandleBordersRequest) => void;
  /** The Borders… picker is open: the handle stays, as its anchor. */
  pinned: boolean;
}) {
  const { t } = useTranslation();
  const editor = useBlockNoteEditor();
  const layerRef = useRef<HTMLDivElement>(null);
  const outlineRef = useRef<HTMLDivElement>(null);
  const handleRef = useRef<HTMLButtonElement>(null);
  const firstRef = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<HandlePlacement | null>(null);
  const openRef = useRef(false);
  const pinnedRef = useRef(pinned);
  const openedBy = useRef<"mouse" | "keyboard">("mouse");
  const after = useRef<After>("stay");
  const selBox = useRef<Box | null>(null);

  // Focus on its way back from the menu (to the handle or the cell): the
  // marks stay meanwhile, not fading out and in again.
  const returning = useRef(false);

  const close = useCallback((then: After) => {
    after.current = then;
    returning.current = then !== "stay" && openedBy.current === "keyboard";
    openRef.current = false;
    setOpen(false);
  }, []);

  /** Places (or hides) the outline and the handle from the selection. */
  const layout = useCallback(() => {
    const view = editor.prosemirrorView as EditorView | undefined;
    const layer = layerRef.current;
    const ol = outlineRef.current;
    const h = handleRef.current;
    if (!view || !layer || !ol || !h) return;
    const m = editor.isEditable ? measure(view) : null;
    const active =
      view.hasFocus() ||
      openRef.current ||
      pinnedRef.current ||
      returning.current ||
      document.activeElement === h ||
      Boolean(document.activeElement?.closest("[data-note-cell-popup]"));
    if (!m || !active || m.outer?.hasAttribute("data-object-selected")) {
      selBox.current = null;
      setOn(ol, false);
      setOn(h, false);
      h.tabIndex = -1;
      if (openRef.current) close("stay");
      return;
    }
    const L = layer.getBoundingClientRect();
    const box = m.wrap.getBoundingClientRect();
    const port = viewport(m.wrap);
    const { sel } = m;
    selBox.current = sel;
    const clip = outlineClip(sel, box);
    Object.assign(ol.style, {
      left: `${sel.left - L.left}px`,
      top: `${sel.top - L.top}px`,
      width: `${sel.right - sel.left}px`,
      height: `${sel.bottom - sel.top}px`,
      clipPath: clip.left || clip.right ? `inset(0 ${clip.right}px 0 ${clip.left}px)` : "",
    });
    setOn(ol, true);
    const spot = handleSpot(sel, {
      left: box.left,
      right: box.right,
      top: Math.max(box.top, port.top),
      bottom: Math.min(box.bottom, port.bottom),
    });
    h.style.left = `${spot.x - L.left}px`;
    h.style.top = `${spot.y - L.top}px`;
    setOn(h, spot.visible);
    h.tabIndex = spot.visible ? 0 : -1;
    if (!spot.visible && openRef.current) close("stay");
  }, [editor, close]);

  const openMenu = useCallback(
    (by: "mouse" | "keyboard") => {
      const h = handleRef.current;
      if (!h?.hasAttribute("data-on")) return;
      openedBy.current = by;
      after.current = "stay";
      setPlacement(besideHandle(h, () => selBox.current, MENU_SIZE));
      openRef.current = true;
      setOpen(true);
    },
    [],
  );

  useEffect(() => {
    const view = editor.prosemirrorView as EditorView | undefined;
    if (!view) return;
    const offSel = editor.onSelectionChange(layout);
    const offChange = editor.onChange(layout);
    const onScroll = () => layout();
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    window.addEventListener("resize", onScroll);
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(onScroll) : null;
    ro?.observe(view.dom);
    // Focus leaving the editor ends the selection's marks (focusout runs
    // before the next element has the focus).
    const onFocus = () => queueMicrotask(layout);
    document.addEventListener("focusin", onFocus);
    document.addEventListener("focusout", onFocus);
    // A drag: the handle takes no pointer events until the button is up.
    const layer = layerRef.current;
    // On the window: the pointer down does not reach the editor's own
    // capture listeners (one ahead stops it there).
    const down = (e: PointerEvent) => {
      if (e.button === 0 && e.target instanceof Node && view.dom.contains(e.target)) {
        layer?.toggleAttribute("data-dragging", true);
      }
    };
    const up = () => {
      if (layer?.hasAttribute("data-dragging")) {
        layer.removeAttribute("data-dragging");
        layout();
      }
    };
    window.addEventListener("pointerdown", down, true);
    document.addEventListener("pointerup", up, true);
    document.addEventListener("pointercancel", up, true);
    // The keyboard paths (ahead of the editor's keymaps and the app's
    // Ctrl+/ shortcut).
    let noContextMenu = 0;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (openRef.current && e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close("stay");
        return;
      }
      if (!isCellMenuKey(e) || !handleRef.current?.hasAttribute("data-on")) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.key !== "/") noContextMenu = Date.now();
      openMenu("keyboard");
    };
    const onContextMenu = (e: MouseEvent) => {
      if (Date.now() - noContextMenu < 800) e.preventDefault();
    };
    view.dom.addEventListener("keydown", onKey, true);
    view.dom.addEventListener("contextmenu", onContextMenu, true);
    layout();
    return () => {
      offSel?.();
      offChange?.();
      document.removeEventListener("scroll", onScroll, { capture: true });
      window.removeEventListener("resize", onScroll);
      ro?.disconnect();
      document.removeEventListener("focusin", onFocus);
      document.removeEventListener("focusout", onFocus);
      window.removeEventListener("pointerdown", down, true);
      document.removeEventListener("pointerup", up, true);
      document.removeEventListener("pointercancel", up, true);
      view.dom.removeEventListener("keydown", onKey, true);
      view.dom.removeEventListener("contextmenu", onContextMenu, true);
    };
  }, [editor, layout, openMenu, close]);

  // The picker opening or closing changes whether the handle stays.
  useLayoutEffect(() => {
    pinnedRef.current = pinned;
    layout();
  }, [pinned, open, layout]);

  const can = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const state = e.prosemirrorState;
      const sel = tableSelection(state);
      return {
        merge: canMerge(state),
        split: canSplit(state),
        filled: filledCells(state).length,
        align: selectedAlignment(state),
        blockId: sel?.blockId ?? null,
        rect: sel ? `${sel.rect.r0},${sel.rect.c0},${sel.rect.r1},${sel.rect.c1}` : null,
      };
    },
  });

  const focusEditor = () => (editor.prosemirrorView as EditorView | undefined)?.focus();

  /** A menu item: the menu closes, then the command runs. */
  const act = (entry: Entry) => {
    if (entry.disabled) return;
    // Borders… hands the focus to its picker.
    close(entry.key === "borders" ? "stay" : openedBy.current === "keyboard" ? "editor" : "stay");
    switch (entry.key) {
      case "merge":
        mergeSelected(editor);
        return;
      case "split":
        splitSelected(editor);
        return;
      case "borders": {
        const h = handleRef.current;
        const sel = tableSelection(editor.prosemirrorState);
        if (!h || !sel) return;
        onBorders({
          anchor: h,
          blockId: sel.blockId,
          selection: sel.rect,
          placement: besideHandle(h, () => selBox.current, PICKER_SIZE),
        });
        return;
      }
      case "clear": {
        const cleared = clearCells(editor);
        if (!cleared) return;
        // The text would be lost: Undo puts it back.
        toast(t("notes:table.cleared", "Cleared {{count}} cells", { count: cleared.cells.length }), {
          action: { label: t("notes:table.undo", "Undo"), onClick: () => restoreCells(editor, cleared) },
          duration: 6000,
        });
        return;
      }
      case "above":
      case "below":
        insertRow(editor, entry.key);
        return;
      case "delete":
        deleteSelectedRows(editor);
        return;
      default:
        alignSelectedColumns(editor, entry.key.slice("align-".length) as Alignment);
    }
  };

  const align = (a: Alignment): Entry => ({
    key: `align-${a}`,
    icon: a === "left" ? <AlignLeft className={ICON} /> : a === "center" ? <AlignCenter className={ICON} /> : <AlignRight className={ICON} />,
    label:
      a === "left"
        ? t("notes:table.alignLeft", "Align left")
        : a === "center"
          ? t("notes:table.alignCenter", "Align centre")
          : t("notes:table.alignRight", "Align right"),
    checked: can.align === a,
  });

  const groups: Entry[][] = [
    [
      {
        key: "merge",
        icon: <Combine className={ICON} />,
        label: t("notes:table.merge", "Merge cells"),
        disabled: !can.merge,
      },
      {
        key: "split",
        icon: <SquareSplitHorizontal className={ICON} />,
        label: t("notes:table.split", "Split cell"),
        disabled: !can.split,
      },
      {
        key: "borders",
        icon: <Grid2x2 className={ICON} />,
        label: (
          <span className="flex min-w-0 flex-col">
            <span>{t("notes:table.borders", "Borders…")}</span>
            <small className="text-[11px] text-muted-foreground">
              {t("notes:table.bordersHint", "Pick a weight, then click an icon")}
            </small>
          </span>
        ),
      },
      {
        key: "clear",
        icon: <Eraser className={ICON} />,
        label: t("notes:table.clear", "Clear contents"),
        disabled: !can.filled,
      },
    ],
    [
      {
        key: "above",
        icon: <ArrowUp className={ICON} />,
        label: t("notes:table.insertAbove", "Insert above"),
      },
      {
        key: "below",
        icon: <ArrowDown className={ICON} />,
        label: t("notes:table.insertBelow", "Insert below"),
      },
      align("left"),
      align("center"),
      align("right"),
    ],
    [
      {
        key: "delete",
        icon: <Trash2 className={ICON} />,
        label: t("notes:table.deleteRows", "Delete rows"),
      },
    ],
  ];
  const first = groups.flat().find((e) => !e.disabled)?.key;

  const move = (e: KeyboardEvent<HTMLDivElement>) => {
    const list = [...e.currentTarget.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])')];
    if (!list.length) return;
    const i = list.indexOf(document.activeElement as HTMLElement);
    const n = list.length;
    const next: Record<string, number> = {
      ArrowDown: (i + 1) % n,
      ArrowUp: (i - 1 + n) % n,
      Home: 0,
      End: n - 1,
    };
    const j = next[e.key];
    if (j === undefined) return;
    e.preventDefault();
    list[j]!.focus();
  };

  const chord = cellMenuChord();

  return (
    <div ref={layerRef} className="note-cell-layer">
      <div ref={outlineRef} className="note-cell-outline" aria-hidden />
      <button
        ref={handleRef}
        type="button"
        tabIndex={-1}
        className="note-cell-handle"
        aria-label={t("notes:table.cellMenu", "Cell menu ({{keys}})", { keys: chord })}
        title={t("notes:table.cellMenuTitle", "Cell menu · {{keys}}", { keys: chord })}
        aria-haspopup="menu"
        aria-expanded={open}
        // The caret stays in the cell (a mouse-opened menu keeps it there).
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          if (openRef.current) {
            close(document.activeElement === e.currentTarget ? "handle" : "stay");
            return;
          }
          openMenu(e.detail === 0 ? "keyboard" : "mouse");
        }}
      >
        {/* A size- class keeps BlockNote's `.bn-shadcn svg:not([class*="size-"])`
            sizing off it; index.css sets 4 × 14. */}
        <svg viewBox="0 0 4 14" width={4} height={14} className="size-auto" aria-hidden>
          <circle cx="2" cy="2" r="1.35" fill="currentColor" />
          <circle cx="2" cy="7" r="1.35" fill="currentColor" />
          <circle cx="2" cy="12" r="1.35" fill="currentColor" />
        </svg>
      </button>
      {placement && (
        <Popover
          open={open}
          onOpenChange={(next, details) => {
            if (next) return;
            // A press on the handle is its own toggle (its click closes).
            const target = details.event?.target;
            if (details.reason === "outside-press" && target instanceof Node && handleRef.current?.contains(target)) return;
            close(details.reason === "escape-key" ? "handle" : "stay");
          }}
        >
          <PopoverContent
            anchor={placement.anchor}
            side={placement.side}
            align={placement.align}
            sideOffset={placement.sideOffset}
            alignOffset={placement.alignOffset}
            role="menu"
            data-note-cell-popup=""
            aria-label={t("notes:table.cellMenuName", "Cell menu")}
            initialFocus={() => (openedBy.current === "keyboard" ? firstRef.current : false)}
            finalFocus={() => {
              const go = after.current;
              returning.current = false;
              queueMicrotask(layout);
              if (go === "handle" && handleRef.current?.hasAttribute("data-on")) return handleRef.current;
              if (go === "editor" || go === "handle") focusEditor();
              return false;
            }}
            onKeyDown={move}
            className="w-56 origin-(--transform-origin) p-1 duration-150 motion-reduce:animate-none"
          >
            {groups.map((group, gi) => (
              <div key={gi} role="group" className={cn(gi > 0 && "mt-1 border-t border-border pt-1")}>
                {group.map((entry) => {
                  const radio = entry.checked !== undefined;
                  return (
                    <button
                      key={entry.key}
                      ref={entry.key === first ? (el) => void (firstRef.current = el) : undefined}
                      type="button"
                      role={radio ? "menuitemradio" : "menuitem"}
                      aria-checked={radio ? entry.checked : undefined}
                      aria-disabled={entry.disabled || undefined}
                      tabIndex={-1}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => act(entry)}
                      className={cn(
                        "flex w-full cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm outline-none select-none",
                        "transition-colors duration-100 hover:bg-accent focus-visible:bg-accent focus:bg-accent motion-reduce:transition-none",
                        "aria-disabled:pointer-events-none aria-disabled:opacity-50",
                      )}
                    >
                      <span className="text-muted-foreground">{entry.icon}</span>
                      <span className="min-w-0 flex-1">{entry.label}</span>
                      {radio && <Check className={cn(ICON, !entry.checked && "invisible")} aria-hidden />}
                    </button>
                  );
                })}
              </div>
            ))}
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}
