/** The small button at a figure's or a display equation's corner (Plan
 * H5b.1): marks the object whole through the mark popover. It fades in
 * while the object is hovered, or when it takes keyboard focus, and fades
 * back out on leave; it grows from the corner it sits in. */
import { Highlighter, Sigma } from "lucide-react";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export type MarkableObject = "figure" | "equation";

export interface ObjectMarkButtonProps {
  kind: MarkableObject;
  /** The object is hovered: the button shows. */
  shown: boolean;
  /** Out of play (the Box tool is on): hidden and skipped by the keyboard. */
  disabled?: boolean;
  /** Where it sits, in its positioned parent. */
  style: CSSProperties;
  onActivate: (button: HTMLButtonElement) => void;
  /** The pointer is over the button itself (it keeps the object hovered). */
  onHoverChange?: (over: boolean) => void;
}

export function ObjectMarkButton({ kind, shown, disabled = false, style, onActivate, onHoverChange }: ObjectMarkButtonProps) {
  const { t } = useTranslation("notes");
  const label = kind === "figure" ? t("marks.markFigure", "Mark figure") : t("marks.markEquation", "Mark equation");
  const Icon = kind === "figure" ? Highlighter : Sigma;
  return (
    <button
      type="button"
      data-object-mark={kind}
      data-shown={shown && !disabled ? "true" : "false"}
      aria-hidden={disabled || undefined}
      tabIndex={disabled ? -1 : undefined}
      style={style}
      onClick={(e) => {
        e.stopPropagation();
        onActivate(e.currentTarget);
      }}
      // A press here is not the start of a text selection on the page.
      onPointerDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.preventDefault()}
      onPointerEnter={() => onHoverChange?.(true)}
      onPointerLeave={() => onHoverChange?.(false)}
      className={cn(
        "absolute z-20 inline-flex origin-top-right items-center gap-1 rounded-[7px] border border-border bg-popover px-2 py-0.5 font-sans text-[11.5px] whitespace-nowrap text-popover-foreground shadow-sm",
        "scale-95 opacity-0 transition-[opacity,transform] duration-150 ease-out motion-reduce:transition-none",
        "pointer-events-none data-[shown=true]:pointer-events-auto data-[shown=true]:scale-100 data-[shown=true]:opacity-100",
        !disabled && "focus-visible:pointer-events-auto focus-visible:scale-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <Icon aria-hidden className="size-3" />
      {label}
    </button>
  );
}
