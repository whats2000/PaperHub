/** What every reference from the paper shares: a bar in the mark colour,
 * paper serif in muted text, the "From the paper" tag, the translation tag
 * with Show original, the anchor-lost states and the source chip (it opens
 * the reader at the mark, with "back to note"). Its look keeps it apart from
 * the user's own words. In the note card only the clicked reference is
 * tinted and every other one is a Show ↗ jumper; in the notes pane the
 * reference of the mark under the pointer on the paper is tinted. */
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, FileText, Languages, Unlink } from "lucide-react";

import { cn } from "@/lib/utils";
import { truncatedTitle } from "@/lib/truncatedTitle";
import { useCanvasStore } from "@/store/canvas";
import { openMarkInReader, useMarkNavStore } from "@/store/markNav";

import {
  markPieces,
  markSource,
  langShort,
  useColorName,
  useNoteEditorContext,
  whereLabel,
  type MarkProps,
} from "./markProps";

interface FrameProps {
  blockId: string;
  props: MarkProps;
  icon: ReactNode;
  /** The tag's first words, e.g. "From the paper"; the colour name and the
   * section / page follow. */
  label: string;
  /** Extra items at the end of the tag line. */
  tagExtra?: ReactNode;
  children: ReactNode;
}

export function PaperRefFrame({ blockId, props, icon, label, tagExtra, children }: FrameProps) {
  const { t } = useTranslation();
  const colorName = useColorName(props.color);
  const src = markSource(props);
  const pieces = markPieces(props);
  const { card, paperTitle } = useNoteEditorContext();
  const where = whereLabel(src, t, paperTitle);
  const tag = [label, colorName, where].filter(Boolean).join(" · ");
  const bar = props.color ? `var(--hl-${props.color})` : "var(--muted-foreground)";
  const hovered = useMarkNavStore((s) => !card && !!props.markId && s.hovered === props.markId);
  // The card's clicked reference, or (the pane) the mark under the pointer.
  const target = card ? !!props.markId && card.markId === props.markId : hovered;
  const jumper = card && props.markId && !target ? card : null;

  return (
    <div
      data-testid={`paper-ref-${blockId}`}
      data-color={props.color || undefined}
      data-target={target ? "true" : undefined}
      contentEditable={false}
      className={cn(
        "w-full rounded-r-md border-l-[3px] border-(--mc) py-0.5 pr-2 pl-2.5 transition-colors duration-200 ease-out motion-reduce:transition-none",
        target && "bg-[color-mix(in_srgb,var(--mc)_16%,transparent)]",
      )}
      style={{ ["--mc" as string]: bar }}
    >
      <div className="flex flex-wrap items-center gap-1 font-sans text-[10.5px] leading-4 text-muted-foreground">
        {icon}
        <span>{tag}</span>
        {pieces.length > 1 && (
          <span>· {t("notes:ref.pieces", "{{count}} pieces", { count: pieces.length })}</span>
        )}
        {props.lang && (
          <span
            className="ml-1 inline-flex items-center gap-0.5 rounded border border-border px-1"
            title={t("notes:ref.translatedTitle", "Marked on the {{lang}} translation", {
              lang: langShort(props.lang),
            })}
          >
            <b className="font-semibold">文</b>{" "}
            {t("notes:ref.translated", "Translated · {{lang}}", { lang: langShort(props.lang) })}
          </span>
        )}
        {tagExtra}
        {jumper && (
          <button
            type="button"
            className="ml-auto inline-flex items-center gap-0.5 rounded px-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            title={t("notes:noteCard.showHint", "Show this passage in the paper")}
            aria-label={t("notes:noteCard.showNamed", "Show this passage in the paper: {{where}}", { where: tag })}
            onClick={() => jumper.onJump(props.markId)}
          >
            {t("notes:noteCard.show", "Show")}
            <ArrowUpRight className="size-3" aria-hidden />
          </button>
        )}
      </div>
      {props.anchorLost && (
        <div className="mt-1 flex items-center gap-1 rounded-md border border-dashed border-amber-500/60 bg-amber-500/10 px-2 py-1 font-sans text-xs text-amber-700 dark:text-amber-300">
          <Unlink className="size-3 shrink-0" aria-hidden />
          {t(
            "notes:ref.anchorLost",
            "Anchor lost: not found after the paper was re-ingested. Your quote is kept.",
          )}
        </div>
      )}
      {!props.anchorLost && props.lang && props.translationChanged && (
        <div
          data-testid={`translation-changed-${blockId}`}
          className="mt-1 flex items-center gap-1 rounded-md border border-dashed border-amber-500/60 bg-amber-500/10 px-2 py-1 font-sans text-xs text-amber-700 dark:text-amber-300"
        >
          <Languages className="size-3 shrink-0" aria-hidden />
          {t("notes:ref.translationChanged", {
            defaultValue: "Translation changed: these words are no longer in the {{lang}} translation. Your quote is kept.",
            lang: langShort(props.lang),
          })}
        </div>
      )}
      {!props.anchorLost && props.piecesLost > 0 && (
        <div className="mt-1 inline-flex items-center gap-1 rounded-md border border-dashed border-amber-500/60 bg-amber-500/10 px-2 py-0.5 font-sans text-[11px] text-amber-700 dark:text-amber-300">
          <Unlink className="size-3 shrink-0" aria-hidden />
          {t("notes:ref.piecesLost", "{{lost}} of {{total}} pieces lost", {
            lost: props.piecesLost,
            total: pieces.length,
          })}
        </div>
      )}
      {children}
      <RefFooter blockId={blockId} props={props} where={where} />
    </div>
  );
}

function RefFooter({ blockId, props, where }: { blockId: string; props: MarkProps; where: string }) {
  const { t } = useTranslation();
  const { noteId, paperContentId, paperTitle, card } = useNoteEditorContext();
  const [open, setOpen] = useState(false);
  const origId = useId();
  const label = [paperTitle, where].filter(Boolean).join(" · ") || t("notes:ref.thePaper", "The paper");
  const openLabel = t("notes:ref.openInPaper", "Open in the paper");
  // The reader at the mark (scrolled to it, flashed), with "back to note";
  // a reference whose mark is gone opens the paper.
  const openSource = (pid: number) => {
    const opened = props.markId
      ? openMarkInReader(props.markId, { paperId: pid, back: { noteId, blockId } })
      : Promise.resolve(false);
    void opened.then((ok) => {
      if (!ok) useCanvasStore.getState().openPaper(pid);
    });
  };

  return (
    <>
      <div className="mt-0.5 flex flex-wrap items-center gap-1">
        {/* The card is beside the paper already: its references jump instead. */}
        {paperContentId !== null && !card && (
          <button
            type="button"
            className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-background px-2 font-sans text-[11px] text-muted-foreground transition-colors hover:text-foreground"
            aria-label={t("notes:ref.openInPaperNamed", "Open in the paper: {{where}}", { where: label })}
            title={openLabel}
            {...truncatedTitle(label, openLabel)}
            onClick={() => openSource(paperContentId)}
          >
            <FileText className="size-3 shrink-0" aria-hidden />
            <span data-truncate className="min-w-0 truncate">
              {label}
            </span>
          </button>
        )}
        {props.lang && props.orig && (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={origId}
            className="px-1 font-sans text-[11px] text-muted-foreground underline hover:text-foreground"
            onClick={() => setOpen((o) => !o)}
          >
            <Languages className="mr-0.5 inline size-3" aria-hidden />
            {open
              ? t("notes:ref.hideOriginal", "Hide original")
              : t("notes:ref.showOriginal", "Show original")}
          </button>
        )}
      </div>
      {props.lang && props.orig && (
        <div
          id={origId}
          data-testid={`orig-${blockId}`}
          data-state={open ? "open" : "closed"}
          className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
        >
          <div className="min-h-0 overflow-hidden">
            <div className="pt-1 font-paper text-[12.5px] text-muted-foreground">{props.orig}</div>
          </div>
        </div>
      )}
    </>
  );
}
