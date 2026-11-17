/** The popover under a selection in the reader: highlight in one of the four
 * named colours, Quote to note, or Add note (Plan H5b.1). */
import { MessageSquarePlus, TextQuote } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { colorLabel } from "@/components/notes/blocks/markProps";
import { usePresence } from "@/hooks/usePresence";
import { placeSelectionPopover } from "@/lib/cardPlacement";
import { MARK_COLORS } from "@/lib/marks/htmlMarks";
import { cn } from "@/lib/utils";
import type { MarkColor, MarkColorInfo } from "@/types/notes";

export interface SelectionBox {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface MarkPopoverProps {
  open: boolean;
  /** The selection's box, in the reader's coordinates. */
  anchor: SelectionBox | null;
  /** The reader's size: the popover stays inside it. */
  bounds: { width: number; height: number };
  colors: readonly MarkColorInfo[];
  /** A save is under way: the actions wait. */
  busy?: boolean;
  /** The selection was made from the keyboard: focus moves into the popover. */
  autoFocus?: boolean;
  onHighlight: (color: MarkColor) => void;
  onQuote: () => void;
  onAddNote: () => void;
  /** `returnFocus`: Escape from inside the popover — focus goes back to
   * the reader. */
  onClose: (opts?: { returnFocus: boolean }) => void;
}

/** Before the first measure (a popover with four swatches and two actions). */
const ESTIMATE = { width: 340, height: 36 };

export function MarkPopover({
  open,
  anchor,
  bounds,
  colors,
  busy = false,
  autoFocus = false,
  onHighlight,
  onQuote,
  onAddNote,
  onClose,
}: MarkPopoverProps) {
  const { t } = useTranslation("notes");
  const presence = usePresence(open && anchor !== null, 150);
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState(ESTIMATE);
  // Closing keeps the last place, so the popover shrinks back where it was.
  const [shown, setShown] = useState(anchor);
  if (open && anchor && anchor !== shown) setShown(anchor);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !presence.mounted) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    if (w && h && (w !== size.width || h !== size.height)) setSize({ width: w, height: h });
  }, [presence.mounted, size, shown]);

  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const inside = ref.current?.contains(document.activeElement) ?? false;
      onCloseRef.current(inside ? { returnFocus: true } : undefined);
    };
    const onDown = (e: PointerEvent) => {
      if (ref.current && e.target instanceof Node && ref.current.contains(e.target)) return;
      onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  // From the keyboard, focus moves to the first action.
  useEffect(() => {
    if (open && autoFocus) ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open, autoFocus, shown]);

  /** ← / → (and Home / End) move between the actions. */
  const onToolbarKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const buttons = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const to =
      e.key === "ArrowRight" ? (at + 1) % buttons.length
      : e.key === "ArrowLeft" ? (at - 1 + buttons.length) % buttons.length
      : e.key === "Home" ? 0
      : e.key === "End" ? buttons.length - 1
      : null;
    if (to === null || !buttons.length) return;
    e.preventDefault();
    buttons[to]!.focus();
  };

  if (!presence.mounted || !shown) return null;
  const place = placeSelectionPopover({ selection: shown, size, bounds });
  const names = new Map(colors.map((c) => [c.color, c.name]));

  return (
    <div
      ref={ref}
      role="toolbar"
      aria-label={t("marks.popover", "Mark the selection")}
      data-state={presence.state}
      data-side={place.side}
      style={{ top: place.top, left: place.left }}
      className={cn(
        "absolute z-30 flex items-center gap-0.5 rounded-[10px] border border-border bg-popover p-1 text-popover-foreground shadow-md",
        "duration-150 ease-out data-[side=below]:origin-top data-[side=above]:origin-bottom",
        "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
        "data-[state=closed]:pointer-events-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
        "motion-reduce:animate-none",
      )}
      // Keep the reader's selection: a press here must not clear it.
      onMouseDown={(e) => e.preventDefault()}
      onKeyDown={onToolbarKey}
    >
      {MARK_COLORS.map((color) => {
        const name = colorLabel(color, names.get(color), t);
        const label = t("marks.highlightAs", { defaultValue: "Highlight: {{name}}", name });
        return (
          <button
            key={color}
            type="button"
            disabled={busy}
            aria-label={label}
            title={label}
            onClick={() => onHighlight(color)}
            className="group inline-flex h-[26px] items-center gap-0 rounded-full px-[5px] text-xs transition-[gap,background-color] duration-150 ease-out hover:gap-[5px] hover:bg-accent focus-visible:gap-[5px] focus-visible:bg-accent focus-visible:outline-none disabled:opacity-50 motion-reduce:transition-none"
          >
            <span aria-hidden className="block size-3.5 shrink-0 rounded-full" style={{ background: `var(--hl-${color})` }} />
            <span
              aria-hidden
              className="max-w-0 overflow-hidden whitespace-nowrap transition-[max-width] duration-150 ease-out group-hover:max-w-[90px] group-focus-visible:max-w-[90px] motion-reduce:transition-none"
            >
              {name}
            </span>
          </button>
        );
      })}
      <span aria-hidden className="mx-[3px] h-[18px] w-px bg-border" />
      <button
        type="button"
        disabled={busy}
        onClick={onQuote}
        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-[3px] text-[12.5px] transition-colors duration-150 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none disabled:opacity-50"
      >
        <TextQuote aria-hidden className="size-3.5" />
        {t("marks.quote", "Quote to note")}
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={onAddNote}
        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-[3px] text-[12.5px] transition-colors duration-150 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none disabled:opacity-50"
      >
        <MessageSquarePlus aria-hidden className="size-3.5" />
        {t("marks.addNote", "Add note")}
      </button>
    </div>
  );
}
