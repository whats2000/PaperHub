/** The notes toolbar, one line under the tabs. Left: Follow ("Following §2" /
 * "Follow reading"). Right: Focus (its label hides in a narrow pane) and the
 * ⋯ menu with the rarely needed items: the note's details, Export as Markdown
 * or Word, and Shortcuts (the guide grows from the ⋯ button). */
import { Menu } from "@base-ui/react/menu";
import { Ellipsis, FileDown, FileText, Keyboard, Loader2, LocateFixed, ScanEye } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { hasOpenLayer } from "@/lib/openLayer";
import { cn } from "@/lib/utils";
import { useNotesStore } from "@/store/notes";

import { partsOfNote } from "./parts";
import { ShortcutsGuide } from "./ShortcutsGuide";

export type ExportFormat = "md" | "docx";

export interface NotesToolbarProps {
  /** The note shown (null while none is loaded). */
  noteId: number | null;
  /** The section Follow is tracking ("§2"), null when none yet. */
  followLabel: string | null;
  /** False on a note with no paper (a chat's note): Follow folds away. */
  followAvailable?: boolean;
  /** How many chats share the note, when known. */
  sharedBy?: number | null;
  /** Export the note (the item shows busy until it settles); the items
   * wait as "Coming soon" until it is given. */
  onExport?: (format: ExportFormat) => void | Promise<void>;
  /** False when the server has no pandoc: Word is disabled with the reason. */
  docxAvailable?: boolean;
  guideOpen: boolean;
  onGuideOpenChange: (open: boolean) => void;
}

const BUTTON =
  "inline-flex items-center gap-1 rounded-md px-[7px] py-px text-[11.5px] text-muted-foreground transition-[background-color,color,box-shadow] duration-150 ease-out hover:bg-accent hover:text-foreground aria-pressed:bg-accent aria-pressed:text-foreground aria-pressed:shadow-[inset_0_-2px_0_var(--foreground)] disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none";
const ITEM =
  "flex w-full cursor-default items-center gap-2 rounded-md px-2 py-[5px] text-left text-[12.5px] text-foreground outline-none select-none data-[highlighted]:bg-accent data-[disabled]:opacity-50";

function ExportItem(props: {
  icon: ReactNode;
  label: string;
  hint: string;
  busy?: boolean;
  /** Why the item is unavailable, on its own line under the label. */
  reason?: string | null;
  onClick?: () => void;
}) {
  return (
    <Menu.Item
      className={cn(ITEM, props.reason && "flex-wrap")}
      disabled={!props.onClick || !!props.reason}
      closeOnClick={false}
      aria-busy={props.busy || undefined}
      title={props.reason ?? undefined}
      onClick={props.onClick}
    >
      {props.busy ? <Loader2 className="size-3 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden /> : props.icon}
      {props.label}
      <small className="ml-auto pl-2.5 text-[11px] text-muted-foreground">{props.hint}</small>
      {props.reason && (
        <span className="basis-full pl-5 text-[11px] whitespace-normal text-muted-foreground">{props.reason}</span>
      )}
    </Menu.Item>
  );
}

export function NotesToolbar({
  noteId,
  followLabel,
  followAvailable = true,
  sharedBy = null,
  onExport,
  docxAvailable = true,
  guideOpen,
  onGuideOpenChange,
}: NotesToolbarProps) {
  const { t } = useTranslation();
  const followOn = useNotesStore((s) => s.followOn);
  const setFollowOn = useNotesStore((s) => s.setFollowOn);
  const focusOn = useNotesStore((s) => s.focusOn);
  const setFocusOn = useNotesStore((s) => s.setFocusOn);
  const revision = useNotesStore((s) =>
    noteId === null ? null : (s.notes[noteId]?.note.revision ?? null),
  );
  const moreRef = useRef<HTMLButtonElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState<ExportFormat | null>(null);

  // The item shows busy while the download is made; the menu closes after.
  const runExport =
    onExport &&
    ((format: ExportFormat) => {
      if (busy) return;
      setBusy(format);
      let done: void | Promise<void>;
      try {
        done = onExport(format);
      } catch {
        done = undefined;
      }
      void Promise.resolve(done)
        .catch(() => {})
        .finally(() => {
          setBusy(null);
          setMenuOpen(false);
        });
    });

  // Esc stops Focus, unless it is closing something open over the page (a
  // menu, the slash / @ menu, a dialog): checked before that closes, in the
  // capture phase, since the editor itself takes a bare Esc too.
  useEffect(() => {
    if (!focusOn) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (hasOpenLayer()) return;
      setFocusOn(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [focusOn, setFocusOn]);

  const toggleFocus = () => {
    if (!focusOn && noteId !== null) {
      // Nothing chosen yet: Focus starts on the note's first part.
      const s = useNotesStore.getState();
      const first = partsOfNote(s.notes[noteId]?.blocks ?? [])[0]?.id;
      if (!s.activePart[noteId] && first) s.setActivePart(noteId, first);
    }
    setFocusOn(!focusOn);
  };

  const followText =
    followOn
      ? followLabel
        ? t("notes:toolbar.following", "Following {{section}}", { section: followLabel })
        : t("notes:toolbar.followingAny", "Following")
      : t("notes:toolbar.follow", "Follow reading");
  const meta =
    revision === null
      ? null
      : sharedBy
        ? t("notes:toolbar.sharedBy", "Shared by {{count}} chats · revision {{revision}}", {
            count: sharedBy,
            revision,
          })
        : t("notes:toolbar.revision", "Revision {{revision}}", { revision });
  const soon = t("notes:toolbar.comingSoon", "Coming soon");
  const exporting = t("notes:toolbar.exporting", "Exporting…");
  const focusLabel = t("notes:toolbar.focus", "Focus");

  return (
    <div
      role="toolbar"
      aria-label={t("notes:toolbar.label", "Note tools")}
      className="relative flex shrink-0 items-center gap-1 border-b border-border px-2 py-[3px] whitespace-nowrap"
    >
      {/* Follow folds away sideways (toward the toolbar's start) on a note
          that has no paper to follow, and unfolds back. */}
      <div
        data-follow-slot
        data-state={followAvailable ? "open" : "closed"}
        inert={!followAvailable || undefined}
        aria-hidden={!followAvailable || undefined}
        className="grid min-w-0 grid-cols-[1fr] transition-[grid-template-columns,opacity] duration-200 ease-out data-[state=closed]:grid-cols-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <div className="flex min-w-0 overflow-hidden">
          <button
            type="button"
            aria-pressed={followOn}
            disabled={noteId === null}
            onClick={() => setFollowOn(!followOn)}
            title={
              followOn
                ? t(
                    "notes:toolbar.followingTitle",
                    "The note scrolls to the section you're reading. Click to stop following.",
                  )
                : t(
                    "notes:toolbar.followTitle",
                    "Click to scroll the note along with the section you're reading.",
                  )
            }
            className={cn(BUTTON, "min-w-0")}
          >
            <LocateFixed className="size-3 shrink-0" aria-hidden />
            <span className="truncate">{followText}</span>
          </button>
        </div>
      </div>
      <span className="flex-1" />
      <button
        type="button"
        aria-pressed={focusOn}
        aria-label={focusLabel}
        disabled={noteId === null}
        onClick={toggleFocus}
        title={t(
          "notes:toolbar.focusTitle",
          "Focus: fade the other parts of the note and their marks on the paper (Esc to stop)",
        )}
        className={BUTTON}
      >
        <ScanEye className="size-3 shrink-0" aria-hidden />
        <span className="@max-[380px]:hidden">{focusLabel}</span>
      </button>
      <span className="inline-flex">
        <Menu.Root open={menuOpen} onOpenChange={setMenuOpen}>
          <Menu.Trigger
            ref={moreRef}
            aria-label={t("notes:toolbar.more", "More")}
            title={t("notes:toolbar.more", "More")}
            className={BUTTON}
          >
            <Ellipsis className="size-3" aria-hidden />
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner side="bottom" align="end" sideOffset={4} className="isolate z-50">
              <Menu.Popup className="min-w-[210px] origin-(--transform-origin) rounded-[10px] border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 motion-reduce:animate-none">
                {meta && (
                  <div className="mb-[3px] border-b border-border px-2 pt-[5px] pb-1.5 text-[11.5px] text-muted-foreground">
                    {meta}
                  </div>
                )}
                <ExportItem
                  icon={<FileDown className="size-3 shrink-0" aria-hidden />}
                  label={t("notes:toolbar.exportMd", "Export as Markdown")}
                  hint={
                    !onExport
                      ? soon
                      : busy === "md"
                        ? exporting
                        : t("notes:toolbar.exportMdHint", ".md, images, references.bib")
                  }
                  busy={busy === "md"}
                  onClick={runExport && (() => runExport("md"))}
                />
                <ExportItem
                  icon={<FileText className="size-3 shrink-0" aria-hidden />}
                  label={t("notes:toolbar.exportDocx", "Export as Word")}
                  hint={
                    !onExport ? soon : busy === "docx" ? exporting : t("notes:toolbar.exportDocxHint", ".docx")
                  }
                  busy={busy === "docx"}
                  reason={
                    onExport && !docxAvailable
                      ? t(
                          "notes:toolbar.exportDocxNeedsPandoc",
                          "Needs pandoc on the server: winget install pandoc, or your package manager",
                        )
                      : null
                  }
                  onClick={runExport && (() => runExport("docx"))}
                />
                <Menu.Separator className="mx-1 my-[3px] h-px bg-border" />
                <Menu.Item className={ITEM} onClick={() => onGuideOpenChange(true)}>
                  <Keyboard className="size-3 shrink-0" aria-hidden />
                  {t("notes:toolbar.shortcuts", "Shortcuts")}
                </Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
        {/* The guide grows from the ⋯ button and returns focus to it. */}
        <ShortcutsGuide open={guideOpen} onOpenChange={onGuideOpenChange} anchor={moreRef} />
      </span>
    </div>
  );
}
