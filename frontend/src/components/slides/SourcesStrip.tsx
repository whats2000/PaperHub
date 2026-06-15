import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, FileText, Plus, X } from "lucide-react";

import { useCanvasStore } from "@/store/canvas";
import { getPaperSections } from "@/lib/api";
import { Button } from "@/components/ui/button";
import type { SlideSourceSection } from "@/types/domain";

interface PaperRef {
  paper_content_id: number;
  title: string;
}

interface Props {
  /** The current page's cited sources (deck_slides.source_sections). */
  sources: SlideSourceSection[];
  /** paper_content_id → title, resolved from the session's references. */
  titleByPaperId: Map<number, string>;
  /** When true the strip is the per-slide REFERENCE EDITOR (× to remove, + to
   *  add). Otherwise it is read-only (chips link to the Citation Canvas). */
  editable?: boolean;
  /** Papers selectable in the "Add source" picker (the session's references). */
  references?: PaperRef[];
  /** Persist the slide's full source list (deterministic, no recompile). */
  onSetSources?: (
    sources: { paper_id: number; section_name: string }[],
  ) => void | Promise<void>;
}

/**
 * SourcesStrip — the "Sources (this page)" band under the slide image.
 *
 * Read mode: one chip per cited section; click → Citation Canvas at the cited
 * span. Edit mode (while editing the current frame): a deterministic reference
 * editor — remove a source with ×, add one by picking a paper + a section from
 * its real list. Citation comments are never hand-edited.
 */
export function SourcesStrip({
  sources,
  titleByPaperId,
  editable = false,
  references = [],
  onSetSources,
}: Props) {
  const { t } = useTranslation("slides");
  const openCitation = useCanvasStore((s) => s.openCitation);

  const asPairs = () =>
    sources.map((s) => ({ paper_id: s.paper_id, section_name: s.section_name }));

  const removeAt = (idx: number) => {
    if (!onSetSources) return;
    void onSetSources(asPairs().filter((_, i) => i !== idx));
  };
  const addSource = (paperId: number, section: string) => {
    if (!onSetSources) return;
    void onSetSources([
      ...asPairs(),
      { paper_id: paperId, section_name: section },
    ]);
  };

  return (
    <div className="shrink-0 flex flex-wrap items-center gap-1.5 border-t border-border bg-muted/10 px-3 py-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("sources.heading", "Sources")}
      </span>

      {sources.length === 0 && !editable && (
        <span className="text-[11px] italic text-muted-foreground">
          {t("sources.empty", "Synthesis — no single source")}
        </span>
      )}

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
        if (editable) {
          // Reference editor: a chip with a remove (×) affordance.
          return (
            <span
              key={`${s.paper_id}:${s.section_name}:${i}`}
              className="flex items-center gap-1 rounded border border-border bg-card px-1.5 py-0.5 text-[11px] text-foreground"
            >
              {label}
              <button
                type="button"
                aria-label={t("sources.remove", "Remove this source")}
                onClick={() => removeAt(i)}
                className="ml-0.5 rounded p-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          );
        }
        if (!grounded) {
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

      {editable && (
        <AddSourcePicker references={references} onAdd={addSource} />
      )}
    </div>
  );
}

/** Deterministic "Add source" picker: choose a paper, then a section from that
 *  paper's real section list. */
function AddSourcePicker({
  references,
  onAdd,
}: {
  references: PaperRef[];
  onAdd: (paperId: number, section: string) => void;
}) {
  const { t } = useTranslation("slides");
  const [open, setOpen] = useState(false);
  const [paperId, setPaperId] = useState<number | "">("");
  const [sections, setSections] = useState<string[]>([]);
  const [section, setSection] = useState("");
  const [loading, setLoading] = useState(false);

  const pickPaper = (pid: number) => {
    setPaperId(pid);
    setSection("");
    setSections([]);
    setLoading(true);
    getPaperSections(pid)
      .then((s) => setSections(s))
      .catch(() => setSections([]))
      .finally(() => setLoading(false));
  };

  const confirm = () => {
    if (paperId === "" || !section) return;
    onAdd(Number(paperId), section);
    setOpen(false);
    setPaperId("");
    setSection("");
    setSections([]);
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-0.5 rounded border border-dashed border-border px-1.5 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/10"
      >
        <Plus className="h-3 w-3" />
        {t("sources.add", "Add source")}
      </button>
    );
  }

  return (
    <span className="flex items-center gap-1 rounded border border-border bg-card px-1 py-0.5 text-[11px]">
      <span className="relative inline-flex items-center">
        <select
          aria-label={t("sources.selectPaper", "Select paper")}
          className="max-w-[10rem] truncate appearance-none rounded bg-background py-0.5 pl-1.5 pr-5 text-[11px]"
          value={paperId}
          onChange={(e) => pickPaper(Number(e.target.value))}
        >
          <option value="">{t("sources.selectPaper", "Select paper")}</option>
          {references.map((r) => (
            <option key={r.paper_content_id} value={r.paper_content_id}>
              {r.title}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-1 h-3 w-3 opacity-60" />
      </span>
      <span className="relative inline-flex items-center">
        <select
          aria-label={t("sources.selectSection", "Select section")}
          className="max-w-[10rem] truncate appearance-none rounded bg-background py-0.5 pl-1.5 pr-5 text-[11px] disabled:opacity-50"
          value={section}
          onChange={(e) => setSection(e.target.value)}
          disabled={paperId === "" || loading}
        >
          <option value="">
            {loading
              ? t("sources.loadingSections", "Loading…")
              : t("sources.selectSection", "Select section")}
          </option>
          {sections.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-1 h-3 w-3 opacity-60" />
      </span>
      <Button
        type="button"
        size="icon-xs"
        variant="ghost"
        aria-label={t("sources.confirmAdd", "Add")}
        className="text-primary"
        disabled={paperId === "" || !section}
        onClick={confirm}
      >
        <Plus className="h-3 w-3" />
      </Button>
      <Button
        type="button"
        size="icon-xs"
        variant="ghost"
        aria-label={t("sources.cancelAdd", "Cancel")}
        onClick={() => setOpen(false)}
      >
        <X className="h-3 w-3" />
      </Button>
    </span>
  );
}
