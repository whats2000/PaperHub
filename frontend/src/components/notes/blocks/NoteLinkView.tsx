/** A `[[` link chip, like a Notion page mention: an icon and the target's
 * name (+ " › heading"), or the user's display text, underlined in the text
 * colour (yellow is for citations). A click follows the link; hovering it
 * previews where it goes and the first lines there, with Open and Edit
 * display text; keyboard focus opens it too (Tab moves into it, Enter
 * follows the link). A target since deleted: grey and struck through, the
 * preview says so, a click says it was deleted; one out of reach says "Can't open this
 * right now" with Retry (plain notices, never an error toast). */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import { ArrowUpRight, FileText, Lightbulb, MessageSquare, NotebookPen, Pencil } from "lucide-react";

import { Popover, PopoverContent } from "@/components/ui/popover";
import { openNoteLink } from "@/lib/notes/openNoteLink";
import { cn } from "@/lib/utils";
import { linkTargetKey, noteTargetInfo, useNotesStore, type LinkTargetInfo } from "@/store/notes";
import type { NoteTargetKind } from "@/types/notes";

import { sectionLabel } from "./markProps";

/** The link's note is there (only its heading may be gone). */
function noteAlive(noteId: number): boolean {
  const st = useNotesStore.getState();
  const loaded = st.notes[noteId];
  return loaded ? !loaded.note.deleted_at : st.linkTargets[linkTargetKey("note", noteId)]?.status === "ok";
}

/** Hover: how long before the preview opens, and before it closes. */
const OPEN_DELAY_MS = 250;
const CLOSE_DELAY_MS = 150;
/** Lines of the target the preview shows. */
const PREVIEW_LINES = 3;

export interface NoteLinkProps {
  targetKind: "note" | "paper";
  targetId: number;
  blockId: string;
  sectionKey: string;
  label: string;
  name: string;
}

/** The target as known now: the loaded note if open somewhere, else the
 * session's lookup. */
function useTarget(kind: "note" | "paper", id: number): LinkTargetInfo | undefined {
  const live = useNotesStore((s) => (kind === "note" ? s.notes[id] : undefined));
  const cached = useNotesStore((s) => s.linkTargets[linkTargetKey(kind, id)]);
  const resolve = useNotesStore((s) => s.resolveLinkTarget);
  useEffect(() => {
    if (!live && id > 0) void resolve(kind, id);
  }, [live, kind, id, resolve]);
  return live ? noteTargetInfo(live) : cached;
}

function KindIcon({ kind, noteKind, className }: { kind: "note" | "paper"; noteKind?: NoteTargetKind; className?: string }) {
  const Icon =
    kind === "paper" ? FileText : noteKind === "session" ? MessageSquare : noteKind === "idea" ? Lightbulb : NotebookPen;
  return <Icon className={className} aria-hidden />;
}

function kindLabel(t: TFunction, kind: "note" | "paper", noteKind?: NoteTargetKind): string {
  if (kind === "paper") return t("notes:link.kind.paper", "Paper");
  if (noteKind === "paper") return t("notes:link.kind.paperNote", "Paper note");
  if (noteKind === "session") return t("notes:link.kind.chatNote", "Chat note");
  if (noteKind === "idea") return t("notes:link.kind.ideaNote", "Idea note");
  return t("notes:link.kind.note", "Note");
}

export function NoteLinkView({
  onCommitLabel,
  onLeave,
  ...link
}: NoteLinkProps & {
  /** Editing ended with this display text ("" = follow the target). */
  onCommitLabel: (label: string) => void;
  onLeave?: (side: "before" | "after") => void;
}) {
  const { t } = useTranslation();
  const { targetKind, targetId, blockId, sectionKey, label, name } = link;
  const info = useTarget(targetKind, targetId);
  const isNote = targetKind === "note";
  const blocks = info?.status === "ok" ? (info.blocks ?? []) : [];
  const heading = isNote && blockId ? blocks.find((b) => b.id === blockId) : undefined;
  const headingGone = isNote && !!blockId && info?.status === "ok" && !heading;
  const dead = info?.status === "dead" || headingGone;

  // The target's name now (its title › the heading or section), else the saved one.
  const current =
    info?.status !== "ok" || dead
      ? name
      : isNote
        ? [info.name || t("notes:link.untitled", "Untitled"), heading?.text].filter(Boolean).join(" › ")
        : info.name
          ? [info.name, sectionKey].filter(Boolean).join(" › ")
          : name;
  const shown = label || current || "…";

  // ---- edit the display text ---------------------------------------------
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(shown);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!editing) return;
    field.current?.focus();
    field.current?.select();
  }, [editing]);
  const finish = (side?: "before" | "after") => {
    if (!editing) return;
    setEditing(false);
    const v = draft.trim();
    // Empty, or the target's name left as it was: follow the target.
    const next = v === "" || v === current ? "" : v;
    if (next !== label) onCommitLabel(next);
    if (side) onLeave?.(side);
  };
  const startEdit = () => {
    setPreview(false);
    setDraft(label || current || "");
    setEditing(true);
  };

  // ---- hover preview ------------------------------------------------------
  const chip = useRef<HTMLSpanElement>(null);
  const [preview, setPreview] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const hover = (on: boolean) => {
    clearTimeout(timer.current);
    if (editing) return;
    timer.current = setTimeout(() => setPreview(on), on ? OPEN_DELAY_MS : CLOSE_DELAY_MS);
  };

  const open = () => {
    if (dead || targetId <= 0) return;
    setPreview(false);
    void openNoteLink(link).then((outcome) => {
      // Plain notices, never an error: deleted meanwhile (the chip turns
      // grey in place, the lookup was refreshed), or only out of reach now.
      if (outcome === "gone") {
        toast(
          !isNote
            ? t("notes:link.paperGone", "This paper is no longer in your library")
            : blockId && noteAlive(targetId)
              ? t("notes:link.headingGone", "This heading was deleted")
              : t("notes:link.noteGone", "This note was deleted"),
        );
      } else if (outcome === "unreachable") {
        toast(t("notes:link.cantOpen", "Can't open this right now"), {
          action: { label: t("notes:link.retry", "Retry"), onClick: open },
        });
      }
    });
  };

  // ---- keyboard: focus shows the preview, Tab moves into it --------------
  const previewBox = useRef<HTMLDivElement>(null);
  // Escape in the preview hands focus back without reopening it.
  const quietFocus = useRef(false);
  const focusOut = (e: React.FocusEvent) => {
    const to = e.relatedTarget as Node | null;
    if (to && (chip.current?.contains(to) || previewBox.current?.contains(to))) return;
    hover(false);
  };

  // The first lines where the link lands (below the heading, for one).
  const start = heading ? blocks.indexOf(heading) + 1 : 0;
  const lines = isNote
    ? blocks
        .slice(start)
        .map((b) => b.text.trim())
        .filter(Boolean)
        .slice(0, PREVIEW_LINES)
    : sectionKey
      ? [sectionLabel(sectionKey)]
      : [];
  const goneText = !isNote
    ? t("notes:link.paperGone", "This paper is no longer in your library")
    : headingGone
      ? t("notes:link.headingGone", "This heading was deleted")
      : t("notes:link.noteGone", "This note was deleted");

  return (
    <span contentEditable={false} className="inline-flex items-baseline align-baseline" data-note-link={targetId}>
      <span
        data-state={editing ? "open" : "closed"}
        className="inline-block max-w-[40ch] overflow-hidden whitespace-nowrap align-baseline transition-[max-width,opacity] duration-200 ease-out data-[state=closed]:max-w-0 data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <input
          ref={field}
          aria-label={t("notes:link.text", "Display text")}
          title={t("notes:link.keys", "Enter: done · Esc · empty: the target's name")}
          tabIndex={editing ? 0 : -1}
          value={draft}
          size={Math.max(4, draft.length + 1)}
          className="mr-1 rounded border border-border bg-background px-1 text-xs"
          onChange={(e) => setDraft(e.target.value)}
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
        tabIndex={editing ? -1 : 0}
        data-note-link-chip=""
        data-dead={dead ? "true" : undefined}
        aria-disabled={dead || undefined}
        aria-label={dead ? `${shown} (${goneText})` : t("notes:link.openNamed", "Open {{name}}", { name: shown })}
        className={cn(
          "inline-flex items-baseline gap-0.5 rounded-sm px-px align-baseline underline underline-offset-2 outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
          dead
            ? "text-muted-foreground line-through decoration-muted-foreground/60"
            : "cursor-pointer text-foreground decoration-foreground/40 hover:bg-accent hover:decoration-foreground",
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
        onFocus={() => {
          clearTimeout(timer.current);
          if (quietFocus.current) quietFocus.current = false;
          else if (!editing) setPreview(true);
        }}
        onBlur={focusOut}
        onKeyDown={(e) => {
          if (editing) return;
          if (e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            open();
          } else if (e.key === "Tab" && !e.shiftKey && preview) {
            // The preview is in a portal: Tab steps into its buttons.
            const first = previewBox.current?.querySelector<HTMLElement>("button");
            if (first) {
              e.preventDefault();
              e.stopPropagation();
              first.focus();
            }
          } else if (e.key === "Escape" && preview) {
            e.stopPropagation();
            setPreview(false);
          }
        }}
      >
        <KindIcon kind={targetKind} noteKind={info?.noteKind} className="size-[0.85em] shrink-0 self-center opacity-70" />
        <span>{shown}</span>
      </span>
      <Popover open={preview && !editing} onOpenChange={(o) => !o && setPreview(false)}>
        <PopoverContent
          anchor={chip}
          side="top"
          initialFocus={false}
          finalFocus={false}
          className="w-80 max-w-[90vw] p-2.5 text-xs motion-reduce:animate-none"
          data-testid="note-link-preview"
          ref={previewBox}
          onMouseEnter={() => hover(true)}
          onMouseLeave={() => hover(false)}
          onFocus={() => clearTimeout(timer.current)}
          onBlur={focusOut}
          onKeyDown={(e) => {
            if (e.key !== "Escape") return;
            e.stopPropagation();
            setPreview(false);
            quietFocus.current = true;
            chip.current?.focus();
          }}
        >
          <div className="mb-1 flex items-center gap-1 text-[10.5px] text-muted-foreground">
            <KindIcon kind={targetKind} noteKind={info?.noteKind} className="size-3 shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {kindLabel(t, targetKind, info?.noteKind)} · {current || name}
            </span>
            {!dead && (
              <button
                type="button"
                className="inline-flex h-5 shrink-0 items-center gap-0.5 rounded px-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                onClick={open}
              >
                <ArrowUpRight className="size-3" aria-hidden />
                {t("notes:link.open", "Open")}
              </button>
            )}
            <button
              type="button"
              aria-label={t("notes:link.edit", "Edit display text")}
              title={t("notes:link.edit", "Edit display text")}
              className="inline-flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              onClick={startEdit}
            >
              <Pencil className="size-3" aria-hidden />
            </button>
          </div>
          {dead ? (
            <p className="m-0 text-muted-foreground">{goneText}</p>
          ) : lines.length ? (
            <div className="space-y-0.5 text-[12.5px] leading-normal text-foreground/80">
              {lines.map((line, i) => (
                <p key={i} className="m-0 line-clamp-2">
                  {line}
                </p>
              ))}
            </div>
          ) : isNote && info?.status === "ok" ? (
            <p className="m-0 text-muted-foreground">{t("notes:link.empty", "Nothing written here yet")}</p>
          ) : null}
        </PopoverContent>
      </Popover>
    </span>
  );
}
