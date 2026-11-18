/** The highlighter in the reader bar (Plan H5b.1): a second way to mark,
 * beside the selection popover. While it is on, every drag marks at once in
 * the pen colour, a click on a figure or an equation marks it whole, and a
 * click on a mark erases the piece under it. The four colours open beside
 * the button while it is on; the label names the colour; Escape (once no
 * popover or menu is open) or the button turns it off. Pen and Box turn each
 * other off. */
import { Highlighter } from "lucide-react";
import { useEffect, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { colorLabel } from "@/components/notes/blocks/markProps";
import { MARK_COLORS } from "@/lib/marks/htmlMarks";
import { hasOpenLayer } from "@/lib/openLayer";
import { cn } from "@/lib/utils";
import { useMarksStore } from "@/store/marks";
import type { MarkColor } from "@/types/notes";

export function HighlighterControl({ shown }: { shown: boolean }) {
  const { t } = useTranslation("notes");
  const on = useMarksStore((s) => s.penOn);
  const color = useMarksStore((s) => s.penColor);
  const colors = useMarksStore((s) => s.colors);
  const setPen = useMarksStore((s) => s.setPen);
  const names = new Map(colors.map((c) => [c.color, c.name]));
  const name = (c: MarkColor) => colorLabel(c, names.get(c), t);

  useEffect(() => {
    if (!shown && on) setPen(false);
  }, [shown, on, setPen]);
  useEffect(() => {
    if (!on) return;
    // On the window, after the document's own listeners: an Escape that a
    // popover, menu or dialog took (or one closing an open layer) is theirs.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || hasOpenLayer()) return;
      setPen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [on, setPen]);

  const pen = `var(--hl-${color})`;
  return (
    <span
      data-highlighter
      aria-hidden={!shown || undefined}
      inert={!shown ? true : undefined}
      className={cn(
        "inline-flex items-center gap-0.5 transition-opacity duration-150 motion-reduce:transition-none",
        !shown && "pointer-events-none opacity-0",
      )}
      style={{ ["--pen" as string]: pen }}
    >
      <button
        type="button"
        aria-pressed={on}
        title={t(
          "marks.highlighterHint",
          "Highlighter: drag across words to mark them at once; click a figure or equation to mark it; click a mark to erase it. Esc to stop.",
        )}
        onClick={() => {
          if (!on) window.getSelection()?.removeAllRanges();
          setPen(!on);
        }}
        className={cn(
          "inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium transition-[color,background-color,box-shadow] duration-150 motion-reduce:transition-none",
          on
            ? "bg-foreground/10 text-foreground shadow-[inset_0_-2px_0_var(--pen)]"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <Highlighter aria-hidden className="size-3.5" />
        <GlidingLabel
          text={
            on
              ? t("marks.highlighterOn", { defaultValue: "Highlighter: {{name}}", name: name(color) })
              : t("marks.highlighter", "Highlighter")
          }
        />
      </button>
      {/* The colours open out of the button while the pen is on, and fold
          back into it when it goes off. */}
      <span
        role="group"
        aria-label={t("marks.highlighterColor", "Highlighter colour")}
        data-state={on ? "open" : "closed"}
        aria-hidden={!on || undefined}
        inert={!on ? true : undefined}
        className={cn(
          "inline-flex gap-px overflow-hidden transition-[max-width,opacity] duration-200 ease-out motion-reduce:transition-none",
          on ? "max-w-[320px] opacity-100" : "max-w-0 opacity-0",
        )}
      >
        {(colors.length ? colors.map((x) => x.color) : MARK_COLORS).map((c) => (
          <button
            key={c}
            type="button"
            aria-pressed={c === color}
            aria-label={name(c)}
            title={name(c)}
            onClick={() => setPen(true, c)}
            className="inline-flex h-[22px] items-center rounded px-1 transition-colors duration-150 hover:bg-muted motion-reduce:transition-none"
          >
            <span
              aria-hidden
              className={cn(
                "block size-3 rounded-full transition-shadow duration-150 motion-reduce:transition-none",
                c === color && "shadow-[0_0_0_2px_var(--background),0_0_0_3.5px_var(--foreground)]",
              )}
              style={{ background: `var(--hl-${c})` }}
            />
          </button>
        ))}
      </span>
    </span>
  );
}

/** A label whose width glides to its new text (the colour name coming and
 * going), so the buttons beside it move smoothly instead of jumping. */
function GlidingLabel({ text }: { text: string }) {
  const outer = useRef<HTMLSpanElement>(null);
  const inner = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const o = outer.current;
    const w = inner.current?.offsetWidth ?? 0;
    // Not laid out (hidden, or no layout at all): its natural width.
    if (o) o.style.width = w > 0 ? `${w}px` : "";
  }, [text]);
  return (
    <span
      ref={outer}
      className="inline-block overflow-hidden whitespace-nowrap transition-[width] duration-200 ease-out motion-reduce:transition-none"
    >
      <span ref={inner} className="inline-block">
        {text}
      </span>
    </span>
  );
}
