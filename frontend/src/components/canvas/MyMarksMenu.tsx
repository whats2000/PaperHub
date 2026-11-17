/** "My marks" in the reader bar (Plan H5b.1): the paper's colours — their
 * names, editable in place, how many marks each holds, and whether its marks
 * show on the paper — and the paper's marks grouped by section in reading
 * order; a click on one scrolls the reader to it and flashes it (in the view
 * it was made in). Opens as a dropdown that grows from its button. */
import { useState, type KeyboardEvent } from "react";
import { ChevronDown, Highlighter, Image, Layers, Quote, Sigma, SquareDashed } from "lucide-react";
import { useTranslation } from "react-i18next";

import { colorLabel, sectionLabel } from "@/components/notes/blocks/markProps";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { joinedQuote, readingOrder } from "@/lib/marks/pieces";
import { cn } from "@/lib/utils";
import { openMarkInReader } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";
import type { Mark, MarkColorInfo, MarkPiece } from "@/types/notes";

const NO_MARKS: Mark[] = [];

const KIND_ICON = {
  highlight: Highlighter,
  quote: Quote,
  figure: Image,
  equation: Sigma,
  region: SquareDashed,
} as const;

const isLive = (p: MarkPiece) => !p.erased && !p.removed;
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : Infinity);

/** Where a mark sits in the paper, to sort by: its first piece in reading
 * order — by chunk and offset when it has one, else by page, top and left
 * (a mark made on a PDF page only). */
function readingKey(m: Mark): number[] {
  const first = readingOrder(m.pieces.filter(isLive))[0];
  if (!first) return [2];
  if (typeof first.chunk_start === "number") return [0, first.chunk_start, num(first.text_start), num(first.tr_start)];
  const boxes = Array.isArray(first.rects) ? (first.rects as number[][]) : first.box ? [first.box] : [];
  const top = boxes.length ? Math.min(...boxes.map((b) => num(b[1]))) : Infinity;
  const left = boxes.length ? Math.min(...boxes.map((b) => num(b[0]))) : Infinity;
  return [1, num(first.page), top, left];
}

function byKey(a: number[], b: number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] ?? -Infinity;
    const y = b[i] ?? -Infinity;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

interface MarkSection {
  key: string | null;
  marks: Mark[];
}

/** The marks grouped by section, sections and marks in reading order. */
function marksBySection(marks: readonly Mark[]): MarkSection[] {
  const sorted = marks
    .filter((m) => m.deleted_at === null)
    .map((m) => ({ m, k: readingKey(m) }))
    .sort((a, b) => byKey(a.k, b.k));
  const out: MarkSection[] = [];
  for (const { m } of sorted) {
    const key = m.section_key ?? null;
    const group = out.find((g) => g.key === key);
    if (group) group.marks.push(m);
    else out.push({ key, marks: [m] });
  }
  return out;
}

/** "note 2" for a mark in the second note of its section (from its
 * `part_label`, "<section> · 2"); null in a section of one note. */
function noteIndex(m: Mark): number | null {
  const n = /· (\d+)$/.exec(m.part_label ?? "")?.[1];
  return n ? Number(n) : null;
}

export function MyMarksMenu({ paperId, shown }: { paperId: number; shown: boolean }) {
  const { t } = useTranslation("notes");
  const [open, setOpen] = useState(false);
  const marks = useMarksStore((s) => s.marksByPaper[paperId] ?? NO_MARKS);
  const colors = useMarksStore((s) => s.colors);
  const shownMarks = marks.filter((m) => m.deleted_at === null);
  const sections = marksBySection(shownMarks);

  return (
    <Popover open={open && shown} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-hidden={!shown}
            inert={!shown ? true : undefined}
            className={cn(
              "inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium text-muted-foreground transition-[color,background-color,opacity] duration-150 hover:bg-muted hover:text-foreground data-popup-open:bg-foreground/10 data-popup-open:text-foreground motion-reduce:transition-none",
              !shown && "opacity-0",
            )}
          />
        }
      >
        <Layers aria-hidden className="size-3.5" />
        {t("myMarks.button", "My marks")}
        <span data-testid="my-marks-count" className="tabular-nums">
          {shownMarks.length}
        </span>
        <ChevronDown aria-hidden className="size-3" />
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="end"
        aria-label={t("myMarks.label", "My marks")}
        className="flex max-h-[min(70vh,520px)] w-80 origin-(--transform-origin) flex-col gap-0 p-2 text-[12.5px]"
      >
        <div className="flex justify-between px-0.5 pb-0.5 text-[11.5px] text-muted-foreground">
          <span>{t("myMarks.colours", "Colours — click a name to rename")}</span>
          <span>{t("myMarks.show", "Show")}</span>
        </div>
        {colors.map((c) => (
          <ColorRow key={c.color} info={c} count={shownMarks.filter((m) => m.color === c.color).length} />
        ))}
        <div className="mt-1.5 min-h-0 flex-1 overflow-y-auto border-t border-border pt-1.5">
          {sections.length === 0 ? (
            <p className="px-1 py-1 text-muted-foreground">{t("myMarks.empty", "No marks on this paper yet.")}</p>
          ) : (
            sections.map((s) => (
              <section key={s.key ?? ""} aria-label={s.key ? sectionLabel(s.key) : undefined} className="mb-1">
                <h4 className="flex items-center justify-between px-1 pt-1 text-[11.5px] font-medium text-muted-foreground">
                  <span className="truncate" title={s.key ?? undefined}>
                    {s.key ? `${sectionLabel(s.key)} ${s.key.replace(/^\d+(?:\.\d+)*\s*/, "")}` : t("myMarks.noSection", "Before the first section")}
                  </span>
                  <span className="shrink-0 tabular-nums">{s.marks.length}</span>
                </h4>
                {s.marks.map((m) => (
                  <MarkRow
                    key={m.id}
                    mark={m}
                    colors={colors}
                    onPick={() => {
                      setOpen(false);
                      void openMarkInReader(m.id, { paperId });
                    }}
                  />
                ))}
              </section>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** A colour: its dot, its name (rename in place: Enter or leaving the field
 * saves it, Escape puts it back), its count and whether its marks show. */
function ColorRow({ info, count }: { info: MarkColorInfo; count: number }) {
  const { t } = useTranslation("notes");
  const patchColor = useMarksStore((s) => s.patchColor);
  const name = colorLabel(info.color, info.name, t);
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const next = draft.trim();
    setDraft(null);
    if (next && next !== name) void patchColor(info.color, { name: next });
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
      e.currentTarget.blur();
    } else if (e.key === "Escape" && draft !== null) {
      // Back to the saved name; the menu stays open.
      e.preventDefault();
      e.stopPropagation();
      setDraft(null);
    }
  };
  return (
    <div className="flex items-center gap-2 px-0.5 py-[3px]">
      <span aria-hidden className="size-3 shrink-0 rounded-full" style={{ background: `var(--hl-${info.color})` }} />
      <input
        value={draft ?? name}
        aria-label={t("myMarks.rename", "Name for this colour")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
        className="min-w-0 flex-1 rounded-[5px] border border-transparent bg-transparent px-1.5 py-px text-[12.5px] transition-colors hover:border-border focus:border-border focus:bg-background focus:outline-none"
      />
      <span className="text-[11.5px] text-muted-foreground tabular-nums">{count}</span>
      <input
        type="checkbox"
        checked={info.visible}
        aria-label={t("myMarks.showColor", { defaultValue: "Show {{name}}", name })}
        onChange={(e) => void patchColor(info.color, { visible: e.target.checked })}
        className="size-3.5 accent-foreground"
      />
    </div>
  );
}

function MarkRow({ mark, colors, onPick }: { mark: Mark; colors: readonly MarkColorInfo[]; onPick: () => void }) {
  const { t } = useTranslation("notes");
  const Icon = KIND_ICON[mark.kind] ?? Quote;
  const quote = joinedQuote(mark.pieces).replace(/\s+/g, " ").trim();
  const snippet = quote.length > 90 ? `${quote.slice(0, 90)}…` : quote;
  const color = mark.color;
  const hidden = color !== null && colors.find((c) => c.color === color)?.visible === false;
  const n = noteIndex(mark);
  return (
    <button
      type="button"
      data-testid={`my-mark-${mark.id}`}
      onClick={onPick}
      title={quote}
      className={cn(
        "flex w-full items-start gap-1.5 rounded-[5px] px-1 py-1 text-left transition-[background-color,opacity] duration-150 hover:bg-accent motion-reduce:transition-none",
        hidden && "opacity-50",
      )}
    >
      <Icon
        aria-hidden
        className="mt-0.5 size-3.5 shrink-0"
        style={{ color: color ? `var(--hl-${color})` : "var(--muted-foreground)" }}
      />
      <span className="line-clamp-2 min-w-0 flex-1 font-paper text-[12px] leading-snug">{snippet || "—"}</span>
      {n !== null && (
        <span className="shrink-0 pt-px text-[11px] text-muted-foreground">
          {t("myMarks.note", { defaultValue: "note {{index}}", index: n })}
        </span>
      )}
    </button>
  );
}
