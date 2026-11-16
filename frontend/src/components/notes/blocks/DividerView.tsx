import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { useNotesStore } from "@/store/notes";

import { foldSummary, partsOfNote } from "../parts";
import { useNoteEditorContext } from "./markProps";

/** A divider's line: its fold chevron at the content edge (centred on a
 * reference's bar), the rule darkened when it frames the part you're in, and
 * the folded part's one-line summary, which unfolds it. */
export function DividerView({ id }: { id: string }) {
  const { t } = useTranslation();
  const { noteId } = useNoteEditorContext();
  const blocks = useNotesStore((s) => s.notes[noteId]?.blocks);
  const folded = useNotesStore((s) => (s.folded[noteId] ?? []).includes(id));
  const active = useNotesStore((s) => s.activePart[noteId] ?? null);
  const toggleFold = useNotesStore((s) => s.toggleFold);

  const { summary, edge } = useMemo(() => {
    const parts = partsOfNote(blocks ?? []);
    const mine = parts.find((p) => p.id === id);
    const at = active === null ? -1 : parts.findIndex((p) => p.id === active);
    // The dividers just above and below the active part frame it.
    const framing = at >= 0 && (parts[at]!.dividerId === id || parts[at + 1]?.dividerId === id);
    return { summary: mine ? foldSummary(mine) : { blocks: 0, marks: 0 }, edge: framing };
  }, [blocks, active, id]);

  const blocksText = t("notes:fold.blocks", "{{count}} blocks", { count: summary.blocks });
  const line = summary.marks
    ? `${blocksText} · ${t("notes:fold.marks", "{{count}} marks", { count: summary.marks })}`
    : blocksText;

  return (
    <div className="note-divider w-full" contentEditable={false} data-testid={`divider-${id}`}>
      <div className="flex h-[26px] items-center gap-[2px]">
        <button
          type="button"
          aria-expanded={!folded}
          aria-label={t("notes:fold.fold", "Fold the part below this divider")}
          title={t("notes:fold.foldTitle", "Fold the part below")}
          onClick={() => toggleFold(noteId, id)}
          className={cn(
            "note-fold -ml-2 inline-flex size-5 shrink-0 items-center justify-center rounded-[5px] text-muted-foreground transition-[opacity,rotate] duration-200 ease-out hover:bg-accent hover:text-foreground motion-reduce:transition-none",
            folded && "-rotate-90",
          )}
        >
          <ChevronDown className="size-3" aria-hidden />
        </button>
        <hr
          data-edge={edge ? "true" : undefined}
          className="note-divider-rule m-0 flex-1 border-0 border-t border-t-[color-mix(in_srgb,var(--foreground)_28%,transparent)] transition-[border-color,box-shadow] duration-200 ease-out motion-reduce:transition-none data-[edge=true]:border-t-[color-mix(in_srgb,var(--foreground)_70%,transparent)] data-[edge=true]:shadow-[0_1px_0_color-mix(in_srgb,var(--foreground)_35%,transparent)]"
        />
      </div>
      <div
        data-state={folded ? "open" : "closed"}
        className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <div className="min-h-0 overflow-hidden">
          <button
            type="button"
            tabIndex={folded ? 0 : -1}
            aria-hidden={!folded}
            onClick={() => toggleFold(noteId, id)}
            className="-ml-[44px] inline-flex items-center gap-[5px] border-0 bg-transparent pt-0.5 pr-1.5 pb-1 pl-[46px] text-xs text-muted-foreground hover:text-foreground"
          >
            <ChevronRight className="size-3" aria-hidden />
            {line}
          </button>
        </div>
      </div>
    </div>
  );
}
