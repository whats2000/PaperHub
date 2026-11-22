/** The Borders… picker (the cell menu): set any line between the selected
 * cells, the Google Docs / Sheets way. A preview of the selection's lines
 * and the weight (thin / thick) on top; below, a 2 × 5 toolbar in Sheets'
 * order — all, inner, inner horizontal, inner vertical, outer / left, top,
 * right, bottom, clear. A click sets that set's lines to the weight, or
 * clears them when they already all are; a set the selection has no lines
 * of is disabled. Lines inside a merged cell are never drawn. Each click is
 * one undo step. Arrow keys move across the toolbar (wrapping), Home /
 * End jump, Enter applies; Escape puts the caret back in the cell, as the
 * cell menu's does. It opens beside the ⋮ on the selection (`placement`).
 */
import { closeHistory } from "@tiptap/pm/history";
import type { EditorView } from "@tiptap/pm/view";
import { useEffect, useReducer, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { Popover, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { TableContent } from "@/types/notes";

import type { HandlePlacement } from "./cellHandleGeometry";
import {
  BORDER_SETS,
  SET_PARTS,
  applyBorderSet,
  bordersOf,
  dumpBorders,
  isPressed,
  isUsable,
  segments,
  type BorderSet,
  type Borders,
  type Selection,
} from "./tableBorders";
import { caretIntoCell } from "./tableMerge";

interface PickerEditor {
  getBlock: (id: string) => { type: string; props: unknown; content?: unknown } | undefined;
  updateBlock: (id: string, update: never) => unknown;
  onChange: (cb: () => void) => (() => void) | void;
  _tiptapEditor: { view: EditorView };
}

const LABELS: Record<BorderSet, string> = {
  all: "All borders",
  inner: "Inner borders",
  innerH: "Inner horizontal borders",
  innerV: "Inner vertical borders",
  outer: "Outer borders",
  left: "Left border",
  top: "Top border",
  right: "Right border",
  bottom: "Bottom border",
  clear: "Clear borders",
};

const ICON_LINE: Record<string, string> = {
  top: "M2 2H18",
  bottom: "M2 18H18",
  left: "M2 2V18",
  right: "M18 2V18",
  innerH: "M2 10H18",
  innerV: "M10 2V18",
};
const STROKE = { 1: 1.4, 2: 2.6 } as const;

/** One set's icon: its lines solid at the weight, the others dashed. */
function SetIcon({ set, weight }: { set: BorderSet; weight: 1 | 2 }) {
  const on = new Set(set === "clear" ? [] : SET_PARTS[set]);
  return (
    <svg viewBox="0 0 20 20" className="size-5" aria-hidden fill="none">
      {Object.entries(ICON_LINE).map(([k, d]) =>
        on.has(k as never) ? (
          <path key={k} d={d} stroke="currentColor" strokeWidth={STROKE[weight]} />
        ) : (
          <path key={k} d={d} stroke="currentColor" strokeOpacity={0.45} strokeWidth={1} strokeDasharray="2 2" />
        ),
      )}
    </svg>
  );
}

/** The selection's lines as they are: solid at their weight, dashed where
 * none; lines inside a merged cell are left out. */
function Preview({ b, content, s, label }: { b: Borders; content: TableContent; s: Selection; label: string }) {
  const nr = s.r1 - s.r0 + 1;
  const nc = s.c1 - s.c0 + 1;
  const W = 76;
  const H = 56;
  const pad = 8;
  const cw = Math.min((W - 2 * pad) / nc, 24);
  const ch = Math.min((H - 2 * pad) / nr, 16);
  const x0 = (W - cw * nc) / 2;
  const y0 = (H - ch * nr) / 2;
  const drawn = segments(b, content, s, "all");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-14 w-[76px] shrink-0" role="img" aria-label={label}>
      <rect x={x0} y={y0} width={cw * nc} height={ch * nr} className="fill-muted" />
      {drawn.map((g) => {
        const w = Number((g.axis === "h" ? b.h : b.v)[g.r]?.[g.c] ?? 0);
        const x = x0 + (g.c - s.c0) * cw;
        const y = y0 + (g.r - s.r0) * ch;
        const [x2, y2] = g.axis === "h" ? [x + cw, y] : [x, y + ch];
        return (
          <line
            key={`${g.axis}${g.r}-${g.c}`}
            className={w ? "on stroke-foreground" : "guide stroke-muted-foreground"}
            x1={x}
            y1={y}
            x2={x2}
            y2={y2}
            strokeWidth={w ? STROKE[w as 1 | 2] : 1}
            strokeDasharray={w ? undefined : "2 2"}
            strokeOpacity={w ? 1 : 0.5}
          />
        );
      })}
    </svg>
  );
}

export function TableBordersPicker({
  editor,
  blockId,
  selection,
  anchor,
  placement,
  open,
  onClose,
}: {
  editor: unknown;
  blockId: string;
  selection: Selection;
  anchor: HTMLElement;
  /** Where it opens (beside the handle); under `anchor` without one. */
  placement?: HandlePlacement;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const e = editor as PickerEditor;
  const [, redraw] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const off = e.onChange(() => redraw());
    return () => {
      if (typeof off === "function") off();
    };
  }, [e]);
  const [weight, setWeight] = useState<1 | 2>(1);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const firstUsable = useRef<HTMLButtonElement | null>(null);
  const escaped = useRef(false);

  const block = e.getBlock(blockId);
  const content = block?.type === "table" ? (block.content as TableContent) : null;
  const b = content ? bordersOf((block!.props ?? {}) as Record<string, unknown>, content) : null;
  const usable = (set: BorderSet) => (b && content ? isUsable(b, content, selection, set) : false);
  const first = Math.max(0, BORDER_SETS.findIndex((s) => usable(s)));
  // The roving tab stop: the first usable icon when it opens (a new picker
  // is mounted per request), then wherever the keys move it.
  const [focusAt, setFocusAt] = useState(first);

  const apply = (set: BorderSet) => {
    if (!b || !content || !usable(set)) return;
    const view = e._tiptapEditor.view;
    // Each click its own undo step (the history would join quick ones).
    view.dispatch(closeHistory(view.state.tr));
    e.updateBlock(blockId, { props: { borders: dumpBorders(applyBorderSet(b, content, selection, set, weight)) } } as never);
  };

  const move = (ev: KeyboardEvent<HTMLDivElement>) => {
    const i = buttons.current.findIndex((el) => el === document.activeElement);
    if (i < 0) return;
    const n = BORDER_SETS.length;
    const cols = 5;
    const next: Record<string, number> = {
      ArrowRight: (i + 1) % n,
      ArrowLeft: (i - 1 + n) % n,
      ArrowDown: (i + cols) % n,
      ArrowUp: (i - cols + n) % n,
      Home: 0,
      End: n - 1,
    };
    const j = next[ev.key];
    if (j === undefined) return;
    ev.preventDefault();
    setFocusAt(j);
    buttons.current[j]?.focus();
  };

  const name = (set: BorderSet) => t(`notes:borders.${set}`, LABELS[set]);
  const nr = selection.r1 - selection.r0 + 1;
  const nc = selection.c1 - selection.c0 + 1;

  return (
    <Popover
      open={open}
      onOpenChange={(o, details) => {
        if (o) return;
        escaped.current = details.reason === "escape-key";
        onClose();
      }}
    >
      <PopoverContent
        anchor={placement?.anchor ?? anchor}
        side={placement?.side ?? "bottom"}
        align={placement?.align ?? "end"}
        sideOffset={placement?.sideOffset ?? 4}
        alignOffset={placement?.alignOffset ?? 0}
        role="dialog"
        data-note-cell-popup=""
        aria-label={t("notes:borders.title", "Borders · {{rows}}×{{cols}} cells", { rows: nr, cols: nc })}
        initialFocus={firstUsable}
        finalFocus={() => {
          if (!escaped.current) return anchor.isConnected ? anchor : true;
          caretIntoCell(e._tiptapEditor.view);
          return false;
        }}
        className="w-[252px] origin-(--transform-origin) p-2 motion-reduce:animate-none"
      >
        {b && content ? (
          <>
            <div className="flex items-start gap-2">
              <Preview b={b} content={content} s={selection} label={t("notes:borders.preview", "Preview of the selection's borders")} />
              <div className="flex min-w-0 flex-col gap-1">
                <div className="text-[11px] text-muted-foreground">
                  {t("notes:borders.title", "Borders · {{rows}}×{{cols}} cells", { rows: nr, cols: nc })}
                </div>
                <div role="group" aria-label={t("notes:borders.weight", "Line weight")} className="flex gap-1">
                  {([1, 2] as const).map((w) => (
                    <button
                      key={w}
                      type="button"
                      aria-pressed={weight === w}
                      onClick={() => setWeight(w)}
                      className={cn(
                        "flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[12px] transition-colors duration-150 motion-reduce:transition-none",
                        weight === w ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/60",
                      )}
                    >
                      <svg viewBox="0 0 20 8" className="h-2 w-5" aria-hidden>
                        <line x1="1" y1="4" x2="19" y2="4" stroke="currentColor" strokeWidth={w} />
                      </svg>
                      {w === 1 ? t("notes:borders.thin", "Thin") : t("notes:borders.thick", "Thick")}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div
              role="toolbar"
              aria-label={t("notes:borders.sets", "Border sets")}
              onKeyDown={move}
              className="mt-2 grid grid-cols-5 gap-1"
            >
              {BORDER_SETS.map((set, i) => {
                const ok = usable(set);
                const label = ok ? name(set) : t("notes:borders.unusable", "{{name}} (this selection has none of these lines)", { name: name(set) });
                return (
                  <button
                    key={set}
                    ref={(el) => {
                      buttons.current[i] = el;
                      if (i === first) firstUsable.current = el;
                    }}
                    type="button"
                    aria-label={label}
                    title={label}
                    tabIndex={i === focusAt ? 0 : -1}
                    aria-disabled={ok ? undefined : true}
                    aria-pressed={set === "clear" || !ok ? undefined : isPressed(b, content, selection, set, weight)}
                    onFocus={() => setFocusAt(i)}
                    onClick={() => apply(set)}
                    className={cn(
                      "flex h-8 items-center justify-center rounded-md text-foreground transition-colors duration-150 motion-reduce:transition-none",
                      "hover:bg-accent aria-pressed:bg-accent aria-disabled:cursor-default aria-disabled:opacity-40 aria-disabled:hover:bg-transparent",
                    )}
                  >
                    <SetIcon set={set} weight={weight} />
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
              {t(
                "notes:borders.note",
                "Click an icon again to clear those lines. A merged cell's edge is its whole span; an edge without a border shows dashed only while editing.",
              )}
            </p>
          </>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
