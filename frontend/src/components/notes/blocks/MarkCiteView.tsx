/** An inline citation of a reading mark: the user's own short text (or the
 * quote's start) in citation yellow, with a tiny quote glyph. Hovering it
 * previews the quote and where it is; a click opens the reader at the mark
 * (as a reference block's source chip does); the pencil in the preview
 * edits its text and Turn into reference block switches the mark's form
 * [user, 2026-11-21]. Its mark deleted: a dashed, muted chip (Undo of the
 * delete brings it back as it was). */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pencil, Quote, TextQuote } from "lucide-react";
import "katex/dist/katex.min.css";

import { Popover, PopoverContent } from "@/components/ui/popover";
import { joinedQuote } from "@/lib/marks/pieces";
import { quoteToKatexHtml } from "@/lib/quoteMath";
import { cn } from "@/lib/utils";
import { useCanvasStore } from "@/store/canvas";
import { openMarkInReader } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";

import { citeDefaultText } from "./markCiteText";
import { useNoteEditorContext, whereLabel } from "./markProps";

/** Hover: how long before the preview opens, and before it closes. */
const OPEN_DELAY_MS = 250;
const CLOSE_DELAY_MS = 150;

export function MarkCiteView({
  markId,
  text,
  paperId: ownPaper,
  onCommit,
  onLeave,
  onTurnIntoBlock,
}: {
  markId: string;
  text: string;
  /** The mark's paper (0: the note's paper). */
  paperId: number;
  /** Editing ended with this text ("" = the default). */
  onCommit: (text: string) => void;
  onLeave?: (side: "before" | "after") => void;
  /** Turn into reference block (the mark's paper). */
  onTurnIntoBlock?: (paperId: number | null) => void;
}) {
  const { t } = useTranslation();
  const ctx = useNoteEditorContext();
  const paperId = ownPaper > 0 ? ownPaper : ctx.paperContentId;
  const loaded = useMarksStore((s) => paperId !== null && s.marksByPaper[paperId] !== undefined);
  const mark = useMarksStore((s) =>
    paperId === null ? undefined : s.marksByPaper[paperId]?.find((m) => m.id === markId && m.deleted_at === null),
  );
  useEffect(() => {
    if (paperId !== null && !loaded) void useMarksStore.getState().loadPaper(paperId).catch(() => undefined);
  }, [paperId, loaded]);
  // Deleted (or never there): the chip says so; its text stays.
  const missing = loaded && !mark;
  const quote = mark ? joinedQuote(mark.pieces) : "";
  const fallback = citeDefaultText(quote);
  const shown = text || fallback || (missing ? t("notes:cite.markGone", "Deleted source") : "…");

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(shown);
  const field = useRef<HTMLInputElement>(null);
  // A new chip's default arrives with the mark: the draft follows until typed in.
  const typed = useRef(false);
  useEffect(() => {
    if (editing && !typed.current && !text) setDraft(shown);
  }, [editing, shown, text]);
  useEffect(() => {
    if (!editing) return;
    field.current?.focus();
    field.current?.select();
  }, [editing]);

  const finish = (side?: "before" | "after") => {
    if (!editing) return;
    setEditing(false);
    typed.current = false;
    const v = draft.trim();
    // Empty, or the default left as it was: the default (it follows the mark).
    const next = v === "" || (!text && v === shown) || v === fallback ? "" : v;
    if (next !== text) onCommit(next);
    if (side) onLeave?.(side);
  };
  const startEdit = () => {
    setPreview(false);
    setDraft(shown);
    setEditing(true);
  };

  // ---- hover preview ----------------------------------------------------
  const chip = useRef<HTMLSpanElement>(null);
  const [preview, setPreview] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const hover = (on: boolean) => {
    clearTimeout(timer.current);
    if (editing) return;
    timer.current = setTimeout(() => setPreview(on), on ? OPEN_DELAY_MS : CLOSE_DELAY_MS);
  };
  const quoteHtml = useMemo(() => (quote ? quoteToKatexHtml(quote) : ""), [quote]);
  const where = mark
    ? whereLabel(
        { section: mark.section_key ?? undefined, page: typeof mark.pieces[0]?.page === "number" ? mark.pieces[0].page : undefined },
        t,
        ctx.paperTitle,
      )
    : "";
  const place = [paperId === ctx.paperContentId ? ctx.paperTitle : null, where].filter(Boolean).join(" · ");

  const open = () => {
    if (!mark || paperId === null) return;
    setPreview(false);
    void openMarkInReader(markId, { paperId }).then((ok) => {
      if (!ok) useCanvasStore.getState().openPaper(paperId);
    });
  };

  return (
    <span contentEditable={false} className="inline-flex items-baseline align-baseline" data-mark-cite={markId}>
      <span
        data-state={editing ? "open" : "closed"}
        className="inline-block max-w-[40ch] overflow-hidden whitespace-nowrap align-baseline transition-[max-width,opacity] duration-200 ease-out data-[state=closed]:max-w-0 data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <input
          ref={field}
          aria-label={t("notes:cite.text", "Citation text")}
          title={t("notes:cite.keys", "Enter: done · Esc · empty: the quote's start")}
          tabIndex={editing ? 0 : -1}
          value={draft}
          size={Math.max(4, draft.length + 1)}
          className="mr-1 rounded border border-border bg-background px-1 text-xs"
          onChange={(e) => {
            typed.current = true;
            setDraft(e.target.value);
          }}
          onBlur={() => finish()}
          onKeyDown={(e) => {
            const el = e.currentTarget;
            if (e.key === "Enter" || e.key === "Escape") {
              e.preventDefault();
              finish("after");
            } else if (e.key === "ArrowLeft" && el.selectionStart === 0 && el.selectionEnd === 0) {
              e.preventDefault();
              finish("before");
            } else if (e.key === "ArrowRight" && el.selectionStart === el.value.length) {
              e.preventDefault();
              finish("after");
            }
          }}
        />
      </span>
      <span
        ref={chip}
        role="link"
        tabIndex={-1}
        data-mark-cite-chip=""
        data-missing={missing ? "true" : undefined}
        title={missing ? t("notes:cite.markGoneTitle", "Its marked passage was deleted (Undo brings it back)") : undefined}
        aria-label={
          missing
            ? undefined
            : t("notes:cite.openNamed", "Open the cited passage: {{text}}", { text: shown })
        }
        className={cn(
          "inline-flex items-baseline gap-px rounded-sm px-px align-baseline transition-colors duration-150 motion-reduce:transition-none",
          missing
            ? "text-muted-foreground outline-1 -outline-offset-1 outline-dashed outline-muted-foreground/60"
            : "cursor-pointer bg-yellow-200/70 text-foreground hover:bg-yellow-300/80 dark:bg-yellow-400/25 dark:hover:bg-yellow-400/40",
          editing && "bg-muted",
        )}
        onMouseEnter={() => hover(true)}
        onMouseLeave={() => hover(false)}
        onMouseDown={(e) => {
          if (editing) e.preventDefault();
        }}
        onClick={() => {
          if (!editing) open();
        }}
      >
        <span>{shown}</span>
        <Quote className="size-[0.6em] shrink-0 self-start opacity-60" aria-hidden />
      </span>
      <Popover open={preview && !editing} onOpenChange={(o) => !o && setPreview(false)}>
        <PopoverContent
          anchor={chip}
          side="top"
          initialFocus={false}
          finalFocus={false}
          className="w-80 max-w-[90vw] p-2.5 text-xs"
          data-testid={`mark-cite-preview-${markId}`}
          onMouseEnter={() => hover(true)}
          onMouseLeave={() => hover(false)}
        >
          <div className="mb-1 flex items-center gap-1 text-[10.5px] text-muted-foreground">
            <span className="min-w-0 flex-1 truncate">
              {missing ? t("notes:cite.markGone", "Deleted source") : place || t("notes:ref.thePaper", "The paper")}
            </span>
            {onTurnIntoBlock && mark?.block_props && (
              <button
                type="button"
                aria-label={t("notes:cite.toBlock", "Turn into reference block")}
                title={t("notes:cite.toBlock", "Turn into reference block")}
                className="inline-flex h-5 shrink-0 items-center gap-1 rounded px-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                onClick={() => {
                  setPreview(false);
                  onTurnIntoBlock(paperId);
                }}
              >
                <TextQuote className="size-3" aria-hidden />
                {t("notes:cite.toBlockShort", "Reference block")}
              </button>
            )}
            <button
              type="button"
              aria-label={t("notes:cite.edit", "Edit the citation text")}
              title={t("notes:cite.edit", "Edit the citation text")}
              className="inline-flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              onClick={startEdit}
            >
              <Pencil className="size-3" aria-hidden />
            </button>
          </div>
          {quoteHtml ? (
            <blockquote
              className="m-0 max-h-40 overflow-y-auto font-paper text-[13px] leading-normal whitespace-pre-line text-muted-foreground"
              dangerouslySetInnerHTML={{ __html: quoteHtml }}
            />
          ) : (
            <p className="m-0 text-muted-foreground">
              {t("notes:cite.markGoneTitle", "Its marked passage was deleted (Undo brings it back)")}
            </p>
          )}
        </PopoverContent>
      </Popover>
    </span>
  );
}
