/** The `[[` picker's menu: a compact list (about 300 px wide, 260 px tall,
 * scrolling inside) under small muted group labels, Notes then Papers (or
 * Headings / Paper sections). A row is a small icon, the title on one line
 * and a muted type line under it (Paper note, Chat note, Paper · 2025, or
 * the note a heading is in). A `size: "small"` row is the "nothing matches"
 * hint, drawn as muted text. BlockNote's controller keeps the keys
 * (↑ ↓ Enter Esc) and positions the menu; this draws it. */
import { useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { LoaderCircle } from "lucide-react";
import type { DefaultReactSuggestionItem, SuggestionMenuProps } from "@blocknote/react";

import { cn } from "@/lib/utils";

function Row({
  item,
  index,
  selected,
  onPick,
}: {
  item: DefaultReactSuggestionItem;
  index: number;
  selected: boolean;
  onPick: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Arrow keys move the highlight past the scrolled edge: keep it in view.
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const hint = item.size === "small";
  return (
    <div
      ref={ref}
      id={`bn-suggestion-menu-item-${index}`}
      role="option"
      aria-selected={selected || undefined}
      className={cn(
        "bn-suggestion-menu-item flex cursor-default select-none items-center gap-2 rounded-md px-2 py-1 transition-colors duration-100 hover:bg-accent aria-selected:bg-accent motion-reduce:transition-none",
        hint && "text-xs text-muted-foreground",
      )}
      // Keep the caret in the editor.
      onMouseDown={(e) => e.preventDefault()}
      onClick={onPick}
    >
      {item.icon && (
        <span className="flex shrink-0 text-muted-foreground [&_svg]:size-4" aria-hidden>
          {item.icon}
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span data-link-row-title="" className={cn("block leading-5", hint ? "whitespace-normal" : "truncate")}>
          {item.title}
        </span>
        {item.subtext && (
          <small
            data-link-row-type=""
            className={cn(
              "block text-[11.5px] leading-4 text-muted-foreground",
              hint ? "whitespace-normal" : "truncate",
            )}
          >
            {item.subtext}
          </small>
        )}
      </span>
    </div>
  );
}

export function LinkPickerMenu({
  items,
  loadingState,
  selectedIndex,
  onItemClick,
}: SuggestionMenuProps<DefaultReactSuggestionItem>) {
  const { t } = useTranslation();
  const rows: ReactNode[] = [];
  let group: string | undefined;
  items.forEach((item, i) => {
    if (item.group && item.group !== group) {
      rows.push(
        <div
          key={`group-${i}`}
          role="presentation"
          data-link-picker-group=""
          className="px-2 pb-0.5 pt-1.5 text-[11px] tracking-[0.03em] text-muted-foreground"
        >
          {item.group}
        </div>,
      );
    }
    group = item.group;
    rows.push(
      <Row key={i} item={item} index={i} selected={i === selectedIndex} onPick={() => onItemClick?.(item)} />,
    );
  });
  return (
    <div
      id="bn-suggestion-menu"
      role="listbox"
      data-link-picker=""
      className="bn-suggestion-menu max-h-[260px] w-[300px] max-w-[calc(100vw-20px)] overflow-y-auto overflow-x-hidden rounded-[10px] bg-popover p-1 text-[13px] text-popover-foreground shadow-md ring-1 ring-foreground/10"
    >
      {rows}
      {!items.length && loadingState !== "loaded" && (
        <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
          <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
          {t("notes:link.picker.loading", "Searching…")}
        </div>
      )}
    </div>
  );
}
