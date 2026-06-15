import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";

import { useCanvasStore } from "@/store/canvas";
import type { SlideSourceSection } from "@/types/domain";

interface Props {
  /** The current page's cited sources (deck_slides.source_sections). */
  sources: SlideSourceSection[];
  /** paper_content_id → title, resolved from the session's references. */
  titleByPaperId: Map<number, string>;
}

/**
 * SourcesStrip — the "Sources (this page)" band under the slide image. One chip
 * per section the on-screen slide was written from; click → Citation Canvas at
 * the first cited chunk. An unsourced cite (empty `chunk_ids`) renders muted +
 * non-clickable (the visible "cited a section with no evidence" signal); a
 * structural/title page with no sources shows a quiet empty state.
 */
export function SourcesStrip({ sources, titleByPaperId }: Props) {
  const { t } = useTranslation("slides");
  const openCitation = useCanvasStore((s) => s.openCitation);

  if (sources.length === 0) {
    return (
      <div className="shrink-0 border-t border-border bg-muted/10 px-3 py-1.5">
        <span className="text-[11px] italic text-muted-foreground">
          {t("sources.empty", "Synthesis — no single source")}
        </span>
      </div>
    );
  }

  return (
    <div className="shrink-0 flex flex-wrap items-center gap-1.5 border-t border-border bg-muted/10 px-3 py-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("sources.heading", "Sources")}
      </span>
      {sources.map((s, i) => {
        const title = titleByPaperId.get(s.paper_id) ?? `#${s.paper_id}`;
        const firstChunk = s.chunk_ids[0];
        const lastChunk = s.chunk_ids[s.chunk_ids.length - 1];
        const grounded = firstChunk !== undefined;
        const label = (
          <>
            <FileText className="h-3 w-3 shrink-0 opacity-70" />
            <span className="max-w-[10rem] truncate">{title}</span>
            <span className="opacity-60">§{s.section_name}</span>
          </>
        );
        if (!grounded) {
          // Unsourced — the marker named a section with no evidence.
          return (
            <span
              key={`${s.paper_id}:${s.section_name}:${i}`}
              aria-disabled="true"
              title={t("sources.unsourced", "No source chunks resolved for this section")}
              className="flex items-center gap-1 rounded border border-dashed border-border bg-transparent px-1.5 py-0.5 text-[11px] italic text-muted-foreground/70"
            >
              {label}
            </span>
          );
        }
        return (
          <button
            key={`${s.paper_id}:${s.section_name}:${i}`}
            type="button"
            onClick={() => openCitation(firstChunk, lastChunk)}
            title={t("sources.openHint", "Open this source in the Citation Canvas")}
            className="flex items-center gap-1 rounded border border-border bg-card px-1.5 py-0.5 text-[11px] text-foreground transition-colors hover:border-primary hover:bg-primary/10"
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
