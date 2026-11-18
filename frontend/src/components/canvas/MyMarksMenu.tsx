/** "My marks" in the reader bar (Plan H5b.1): the user's colours — their
 * labels (what each colour means), editable in place, how many marks each
 * holds, whether its marks show on the paper; recolour, reorder, add a
 * labelled colour, delete a custom one no mark uses — and the paper's marks grouped by section in reading
 * order (each with its kind, a snippet and the note it is in), narrowed by
 * kind and colour (the list only; the colours' Show is what changes the paper); a click
 * on one scrolls the reader to it and flashes it (in the view it was made
 * in). Opens as a dropdown that grows from its button. */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ArrowDown, ArrowUp, ChevronDown, Highlighter, Image, Layers, Plus, Quote, Sigma, SquareDashed, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { colorLabel, sectionLabel } from "@/components/notes/blocks/markProps";
import { ColorEditor } from "@/components/canvas/ColorEditor";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { usePresence } from "@/hooks/usePresence";
import { DEFAULT_PALETTE, kindAndStyle, labelProblem, STYLE_NAME } from "@/lib/marks/palette";
import { joinedQuote, markPositions, type PaperChunk } from "@/lib/marks/pieces";
import { cn } from "@/lib/utils";
import { openMarkInReader } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";
import type { Mark, MarkColorInfo, MarkKind } from "@/types/notes";

const NO_MARKS: Mark[] = [];

const KIND_LABEL: Record<MarkKind, string> = {
  text: "Text",
  figure: "Figure",
  equation: "Equation",
  region: "Region",
};

const KIND_ICON = {
  text: Highlighter,
  figure: Image,
  equation: Sigma,
  region: SquareDashed,
} as const;

const KINDS: MarkKind[] = ["text", "figure", "equation", "region"];
const NO_CHUNKS: PaperChunk[] = [];

interface MarkSection {
  key: string | null;
  marks: Mark[];
}

/** "§2 Method"; an unnumbered section is named once ("§ Introduction"). */
function sectionHeading(key: string): string {
  const label = sectionLabel(key);
  const name = key.replace(/^\d+(?:\.\d+)*\s*/, "");
  return label.endsWith(name) ? label : `${label} ${name}`;
}

/** The marks grouped by section, sections and marks in reading order (one
 * position for every kind of anchor: `markPositions`). */
function marksBySection(marks: readonly Mark[], chunks: readonly PaperChunk[]): MarkSection[] {
  const pos = markPositions(marks, chunks);
  const sorted = [...marks].sort((a, b) => (pos.get(a.id) ?? Infinity) - (pos.get(b.id) ?? Infinity));
  const out: MarkSection[] = [];
  for (const m of sorted) {
    const key = m.section_key ?? null;
    const group = out.find((g) => g.key === key);
    if (group) group.marks.push(m);
    else out.push({ key, marks: [m] });
  }
  return out;
}

/** Which note of its section a mark is in (from its `part_label`,
 * "<section> · 2"; a section of one note: 1). */
function noteIndex(m: Mark): number {
  const n = /· (\d+)$/.exec(m.part_label ?? "")?.[1];
  return n ? Number(n) : 1;
}

export function MyMarksMenu({ paperId, shown }: { paperId: number; shown: boolean }) {
  const { t } = useTranslation("notes");
  const [open, setOpen] = useState(false);
  const marks = useMarksStore((s) => s.marksByPaper[paperId] ?? NO_MARKS);
  const colors = useMarksStore((s) => s.colors);
  const shownMarks = useMemo(() => marks.filter((m) => m.deleted_at === null), [marks]);
  // The paper's chunks, in order: where a mark anchored to the text sits.
  const chunkMap = useMarksStore((s) => s.chunksByPaper[paperId]);
  const chunks = useMemo(
    () => (chunkMap ? [...chunkMap.byId.values()].map((c) => ({ id: c.id, section: c.section })) : NO_CHUNKS),
    [chunkMap],
  );
  useEffect(() => {
    if (open) void useMarksStore.getState().ensureChunks(paperId).catch(() => undefined);
  }, [open, paperId]);
  // The kinds listed (the list only).
  const [kinds, setKinds] = useState<ReadonlySet<MarkKind>>(() => new Set(KINDS));
  // The colours left out of the list ("" = no colour); a new colour is listed.
  const [unlisted, setUnlisted] = useState<ReadonlySet<string>>(() => new Set());
  const listed = useMemo(
    () => shownMarks.filter((m) => kinds.has(kindAndStyle(m).kind) && !unlisted.has(m.color ?? "")),
    [shownMarks, kinds, unlisted],
  );
  const sections = useMemo(() => marksBySection(listed, chunks), [listed, chunks]);

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
          <span>{t("myMarks.colours", "Colours — click a label to rename")}</span>
          <span>{t("myMarks.show", "Show")}</span>
        </div>
        <div className="max-h-[190px] shrink-0 overflow-y-auto">
          {colors.map((c, i) => (
            <ColorRow
              key={c.color}
              info={c}
              colors={colors}
              index={i}
              count={shownMarks.filter((m) => m.color === c.color).length}
            />
          ))}
          <AddColor colors={colors} />
        </div>
        <div
          role="group"
          aria-label={t("myMarks.kinds", "Kinds to list")}
          className="mt-1.5 flex flex-wrap items-center gap-1 border-t border-border px-0.5 pt-1.5"
        >
          {KINDS.map((k) => {
            const Icon = KIND_ICON[k];
            const on = kinds.has(k);
            return (
              <button
                key={k}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  setKinds((cur) => {
                    const next = new Set(cur);
                    if (on) next.delete(k);
                    else next.add(k);
                    return next;
                  })
                }
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-px text-[11px] transition-colors duration-150 motion-reduce:transition-none",
                  on ? "bg-foreground/10 text-foreground" : "text-muted-foreground hover:bg-muted",
                )}
              >
                <Icon aria-hidden className="size-3" />
                {t(`myMarks.kind.${k}`, KIND_LABEL[k])}
              </button>
            );
          })}
          {listed.length !== shownMarks.length && (
            <span className="ml-auto text-[11px] text-muted-foreground tabular-nums animate-in fade-in-0 duration-150 motion-reduce:animate-none">
              {t("myMarks.listed", { defaultValue: "{{shown}} of {{count}}", shown: listed.length, count: shownMarks.length })}
            </span>
          )}
        </div>
        <div
          role="group"
          aria-label={t("myMarks.colorsToList", "Colours to list")}
          className="mt-1 flex flex-wrap items-center gap-1 px-0.5"
        >
          {[...colors.map((c) => ({ key: c.color, name: colorLabel(c.color, c.name, t) })), { key: "", name: t("myMarks.noColor", "No colour") }].map(
            ({ key, name }) => {
              const on = !unlisted.has(key);
              return (
                <button
                  key={key || "none"}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    setUnlisted((cur) => {
                      const next = new Set(cur);
                      if (on) next.add(key);
                      else next.delete(key);
                      return next;
                    })
                  }
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-px text-[11px] transition-colors duration-150 motion-reduce:transition-none",
                    on ? "bg-foreground/10 text-foreground" : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn("size-2.5 shrink-0 rounded-full", !key && "border border-muted-foreground")}
                    style={key ? { background: `var(--hl-${key})` } : undefined}
                  />
                  {name}
                </button>
              );
            },
          )}
        </div>
        <div className="mt-1.5 min-h-0 flex-1 overflow-y-auto border-t border-border pt-1.5">
          {sections.length === 0 ? (
            <p className="px-1 py-1 text-muted-foreground">
              {shownMarks.length
                ? t("myMarks.noneOfKind", "No marks of these kinds and colours.")
                : t("myMarks.empty", "No marks on this paper yet.")}
            </p>
          ) : (
            sections.map((s) => (
              <section key={s.key ?? ""} aria-label={s.key ? sectionLabel(s.key) : undefined} className="mb-1">
                <h4 className="flex items-center justify-between px-1 pt-1 text-[11.5px] font-medium text-muted-foreground">
                  <span className="truncate" title={s.key ?? undefined}>
                    {s.key ? sectionHeading(s.key) : t("myMarks.noSection", "Before the first section")}
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

/** Height-animated inline expander (the colour editor under a row). */
function Expander({ open, children }: { open: boolean; children: React.ReactNode }) {
  const p = usePresence(open, 200);
  if (!p.mounted) return null;
  return (
    <div
      data-state={p.state}
      className="grid transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 data-[state=open]:grid-rows-[1fr] data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:transition-none motion-reduce:animate-none"
    >
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  );
}

/** "+ Add a colour": a labelled colour at the end of the palette. */
function AddColor({ colors }: { colors: readonly MarkColorInfo[] }) {
  const { t } = useTranslation("notes");
  const addColor = useMarksStore((s) => s.addColor);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setError(null);
          setOpen((o) => !o);
        }}
        className="mt-0.5 inline-flex items-center gap-1 rounded-[5px] px-1 py-0.5 text-[12px] text-muted-foreground transition-colors duration-150 hover:bg-accent hover:text-foreground"
      >
        <Plus aria-hidden className="size-3.5" />
        {t("colors.add", "Add a colour")}
      </button>
      <Expander open={open}>
        <ColorEditor
          colors={colors}
          busy={busy}
          error={error}
          saveLabel={t("colors.save", "Add")}
          onCancel={() => setOpen(false)}
          onSave={(hex, name) => {
            setBusy(true);
            addColor(hex, name)
              .then(() => setOpen(false))
              .catch(() => setError(t("colors.addFailed", "Couldn't add the colour.")))
              .finally(() => setBusy(false));
          }}
        />
      </Expander>
    </>
  );
}

/** A colour: its swatch (click: recolour and rename), its label (rename in
 * place: Enter or leaving the field saves it, Escape puts it back), its
 * count, move up / down, delete (a custom colour no mark uses) and whether
 * its marks show. */
function ColorRow({
  info,
  colors,
  index,
  count,
}: {
  info: MarkColorInfo;
  colors: readonly MarkColorInfo[];
  index: number;
  count: number;
}) {
  const { t } = useTranslation("notes");
  const patchColor = useMarksStore((s) => s.patchColor);
  const reorder = useMarksStore((s) => s.reorderColors);
  const deleteColor = useMarksStore((s) => s.deleteColor);
  const name = colorLabel(info.color, info.name, t);
  const [draft, setDraft] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  // The edit being saved, read once: Enter leaves the field, and leaving it
  // saves, so a save never goes twice.
  const draftRef = useRef<string | null>(null);
  const edit = (v: string | null) => {
    draftRef.current = v;
    setDraft(v);
  };
  const commit = () => {
    const d = draftRef.current;
    if (d === null) return;
    edit(null);
    const next = d.trim().replace(/\s+/g, " ");
    // A label is required and unique: anything else puts the old one back.
    if (next !== name && !labelProblem(next, colors, info.color)) void patchColor(info.color, { name: next });
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
      edit(null);
    }
  };
  const move = (by: number) => {
    const order = colors.map((c) => c.color);
    const [key] = order.splice(index, 1);
    order.splice(index + by, 0, key!);
    void reorder(order);
  };
  const problem = draft !== null ? labelProblem(draft, colors, info.color) : null;
  const iconBtn =
    "inline-flex size-5 items-center justify-center rounded text-muted-foreground transition-colors duration-150 hover:bg-accent hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent";
  return (
    <div data-testid={`color-row-${info.color}`}>
      <div className="flex items-center gap-1.5 px-0.5 py-[3px]">
        <button
          type="button"
          aria-expanded={editing}
          aria-label={t("myMarks.recolour", { defaultValue: "Change colour of {{name}}", name })}
          onClick={() => setEditing((e) => !e)}
          className="size-3.5 shrink-0 rounded-full ring-offset-1 transition-shadow hover:ring-2 hover:ring-border"
          style={{ background: `var(--hl-${info.color})` }}
        />
        <input
          value={draft ?? name}
          aria-label={t("myMarks.rename", "Label for this colour")}
          aria-invalid={problem !== null || undefined}
          onChange={(e) => edit(e.target.value)}
          onBlur={commit}
          onKeyDown={onKey}
          className="min-w-0 flex-1 rounded-[5px] border border-transparent bg-transparent px-1.5 py-px text-[12.5px] transition-colors hover:border-border focus:border-border focus:bg-background focus:outline-none aria-invalid:border-destructive"
        />
        <span className="text-[11.5px] text-muted-foreground tabular-nums">{count}</span>
        <button type="button" disabled={index === 0} aria-label={t("myMarks.moveUp", { defaultValue: "Move {{name}} up", name })} onClick={() => move(-1)} className={iconBtn}>
          <ArrowUp aria-hidden className="size-3" />
        </button>
        <button
          type="button"
          disabled={index === colors.length - 1}
          aria-label={t("myMarks.moveDown", { defaultValue: "Move {{name}} down", name })}
          onClick={() => move(1)}
          className={iconBtn}
        >
          <ArrowDown aria-hidden className="size-3" />
        </button>
        {!(info.builtin ?? info.color in DEFAULT_PALETTE) && (
          <button
            type="button"
            disabled={count > 0}
            title={count > 0 ? t("myMarks.inUse", "Marks use this colour") : undefined}
            aria-label={t("myMarks.deleteColor", { defaultValue: "Delete {{name}}", name })}
            onClick={() => void deleteColor(info.color)}
            className={iconBtn}
          >
            <Trash2 aria-hidden className="size-3" />
          </button>
        )}
        <input
          type="checkbox"
          checked={info.visible}
          aria-label={t("myMarks.showColor", { defaultValue: "Show {{name}}", name })}
          onChange={(e) => void patchColor(info.color, { visible: e.target.checked })}
          className="size-3.5 accent-foreground"
        />
      </div>
      <Expander open={editing}>
        <ColorEditor
          colors={colors}
          except={info.color}
          initialHex={info.hex || "#3b7dd8"}
          initialName={name}
          saveLabel={t("colors.saveEdit", "Save")}
          onCancel={() => setEditing(false)}
          onSave={(hex, label) => {
            setEditing(false);
            void patchColor(info.color, { hex, ...(label !== name ? { name: label } : {}) });
          }}
        />
      </Expander>
    </div>
  );
}

function MarkRow({ mark, colors, onPick }: { mark: Mark; colors: readonly MarkColorInfo[]; onPick: () => void }) {
  const { t } = useTranslation("notes");
  const { kind, style } = kindAndStyle(mark);
  const Icon = KIND_ICON[kind] ?? Quote;
  const quote = joinedQuote(mark.pieces).replace(/\s+/g, " ").trim();
  const snippet = quote.length > 90 ? `${quote.slice(0, 90)}…` : quote;
  const color = mark.color;
  const info = color !== null ? colors.find((c) => c.color === color) : undefined;
  const hidden = info?.visible === false;
  const label = info ? colorLabel(info.color, info.name, t) : null;
  const what = [
    t(`myMarks.kind.${kind}`, KIND_LABEL[kind]),
    ...(style ? [t(`marks.style.${style}`, STYLE_NAME[style])] : []),
    ...(label ? [label] : []),
  ].join(" · ");
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
        role="img"
        aria-label={what}
        data-kind={kind}
        data-style={style ?? undefined}
        className="mt-0.5 size-3.5 shrink-0"
        style={{ color: color ? `var(--hl-${color})` : "var(--muted-foreground)" }}
      />
      <span className="line-clamp-2 min-w-0 flex-1 font-paper text-[12px] leading-snug">{snippet || "—"}</span>
      <span className="shrink-0 pt-px text-[11px] text-muted-foreground">
        {t("myMarks.note", { defaultValue: "note {{index}}", index: n })}
      </span>
    </button>
  );
}
