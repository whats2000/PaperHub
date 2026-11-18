/** The popover under a selection in the reader (Plan H5b.1):
 *
 *   [ Highlight | Underline | Wavy | Bar ]  ● ● ● ●  ○ (no colour)  ＋
 *
 * The style is a choice (remembered); a swatch marks the selection in that
 * style and colour, so every mark goes to the note at once (its note card
 * opens for the writing). A figure, an equation or a box has no style: only
 * the colours. ＋ adds a labelled colour (ColorEditor) and marks with it. */
import { Plus } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ColorEditor } from "@/components/canvas/ColorEditor";
import { colorLabel } from "@/components/notes/blocks/markProps";
import { usePresence } from "@/hooks/usePresence";
import { placeSelectionPopover } from "@/lib/cardPlacement";
import { DEFAULT_PALETTE, MARK_STYLES, STYLE_NAME } from "@/lib/marks/palette";
import { cn } from "@/lib/utils";
import { useMarksStore } from "@/store/marks";
import type { MarkColor, MarkColorInfo, MarkStyle } from "@/types/notes";

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
  /** What is being marked: a passage (the default), or a figure, an
   * equation or a box taken whole — then the plain action reads "Add to
   * note" (there is no passage to quote). */
  target?: "selection" | "figure" | "equation" | "box";
  /** Placed in the window (`anchor` and `bounds` in window coordinates):
   * a selection outside the reader, e.g. in the translation card. */
  fixed?: boolean;
  /** Mark it: in `style` (null for a figure, an equation or a box) and
   * `color` (null: no colour). */
  onMark: (style: MarkStyle | null, color: MarkColor | null) => void;
  /** `returnFocus`: Escape from inside the popover — focus goes back to
   * the reader. */
  onClose: (opts?: { returnFocus: boolean }) => void;
}

/** Before the first measure (four styles, the swatches, ○ and ＋). */
const ESTIMATE = { width: 400, height: 36 };

const STYLE_ICON: Record<MarkStyle, React.ReactNode> = {
  highlight: <span className="rounded-[2px] bg-current/25 px-0.5">ab</span>,
  underline: <span className="underline decoration-2 underline-offset-2">ab</span>,
  wavy: <span className="underline decoration-wavy decoration-[1.5px] underline-offset-2">ab</span>,
  bar: <span className="border-l-[2.5px] border-current pl-1">ab</span>,
};

export function MarkPopover({
  open,
  anchor,
  bounds,
  colors,
  busy = false,
  autoFocus = false,
  target = "selection",
  fixed = false,
  onMark,
  onClose,
}: MarkPopoverProps) {
  const { t } = useTranslation("notes");
  const style = useMarksStore((s) => s.markStyle);
  const setStyle = useMarksStore((s) => s.setMarkStyle);
  const addColor = useMarksStore((s) => s.addColor);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const picker = usePresence(adding && open, 200);
  if (!open && adding) setAdding(false);
  const styled = target === "selection";
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
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // Handled here: a mode the Escape would also stop (Box) waits for the next.
      e.preventDefault();
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
    if (e.target instanceof HTMLInputElement) return;
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
      aria-label={
        target === "figure"
          ? t("marks.popoverFigure", "Mark the figure")
          : target === "equation"
            ? t("marks.popoverEquation", "Mark the equation")
            : target === "box"
              ? t("marks.popoverBox", "Mark the box")
              : t("marks.popover", "Mark the selection")
      }
      data-state={presence.state}
      data-side={place.side}
      style={{ top: place.top, left: place.left }}
      className={cn(
        fixed ? "fixed z-[60]" : "absolute z-30",
        "flex items-center gap-0.5 rounded-[10px] border border-border bg-popover p-1 text-popover-foreground shadow-md",
        "duration-150 ease-out data-[side=below]:origin-top data-[side=above]:origin-bottom",
        "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
        "data-[state=closed]:pointer-events-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
        "motion-reduce:animate-none",
      )}
      // Keep the reader's selection: a press here must not clear it.
      onMouseDown={(e) => e.preventDefault()}
      onKeyDown={onToolbarKey}
    >
      {styled && (
        <div role="group" aria-label={t("marks.styleGroup", "Style")} className="flex items-center gap-0.5">
          {MARK_STYLES.map((st) => (
            <button
              key={st}
              type="button"
              aria-pressed={style === st}
              aria-label={t(`marks.style.${st}`, STYLE_NAME[st])}
              title={t(`marks.style.${st}`, STYLE_NAME[st])}
              onClick={() => setStyle(st)}
              className={cn(
                "inline-flex h-[26px] items-center rounded-md px-1.5 text-[12px] text-muted-foreground transition-colors duration-150 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none",
                style === st && "bg-accent text-foreground",
              )}
            >
              {STYLE_ICON[st]}
            </button>
          ))}
          <span aria-hidden className="mx-[3px] h-[18px] w-px bg-border" />
        </div>
      )}
      {(colors.length ? colors.map((c) => c.color) : Object.keys(DEFAULT_PALETTE)).map((color) => {
        const name = colorLabel(color, names.get(color), t);
        const label = styled
          ? t("marks.markAs", { defaultValue: "{{style}}: {{name}}", style: t(`marks.style.${style}`, STYLE_NAME[style]), name })
          : t("marks.objectAs", { defaultValue: "Mark: {{name}}", name });
        return (
          <button
            key={color}
            type="button"
            disabled={busy}
            aria-label={label}
            title={name}
            onClick={() => onMark(styled ? style : null, color)}
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
      {(() => {
        // A highlight needs a colour: with it chosen, ○ is off.
        const off = busy || (styled && style === "highlight");
        const label = off && !busy
          ? t("marks.noColorHighlight", "A highlight needs a colour")
          : styled
            ? t("marks.markAsNoColor", { defaultValue: "{{style}}: no colour", style: t(`marks.style.${style}`, STYLE_NAME[style]) })
            : t("marks.objectNoColor", "Mark: no colour");
        return (
          <button
            type="button"
            disabled={off}
            aria-label={label}
            title={label}
            onClick={() => onMark(styled ? style : null, null)}
            className="inline-flex h-[26px] items-center rounded-full px-[5px] transition-colors duration-150 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none disabled:opacity-35"
          >
            <span aria-hidden className="block size-3.5 rounded-full border-[1.5px] border-muted-foreground" />
          </button>
        );
      })()}
      <button
        type="button"
        disabled={busy}
        aria-expanded={adding}
        aria-label={t("colors.add", "Add a colour")}
        title={t("colors.add", "Add a colour")}
        onClick={() => {
          setAddError(null);
          setAdding((a) => !a);
        }}
        className="inline-flex h-[26px] items-center rounded-full px-[5px] text-muted-foreground transition-colors duration-150 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none disabled:opacity-50"
      >
        <Plus aria-hidden className="size-3.5" />
      </button>
      {picker.mounted && (
        <div
          data-state={picker.state}
          className={cn(
            "absolute right-0 top-[calc(100%+4px)] origin-top-right rounded-[10px] border border-border bg-popover p-1 shadow-md",
            "duration-200 ease-out data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "data-[state=closed]:pointer-events-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
            "motion-reduce:animate-none",
          )}
          // Typing here must not clear the reader's selection either.
          onMouseDown={(e) => e.stopPropagation()}
        >
          <ColorEditor
            colors={colors}
            busy={saving}
            error={addError}
            saveLabel={t("colors.addAndMark", "Add and mark")}
            onCancel={() => setAdding(false)}
            onSave={(hex, name) => {
              setSaving(true);
              setAddError(null);
              addColor(hex, name)
                .then((key) => {
                  setAdding(false);
                  onMark(styled ? style : null, key);
                })
                .catch(() => setAddError(t("colors.addFailed", "Couldn't add the colour.")))
                .finally(() => setSaving(false));
            }}
          />
        </div>
      )}
    </div>
  );
}
