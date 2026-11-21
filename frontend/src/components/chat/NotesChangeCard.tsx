/** The agent's change to a note, in the chat (H5b): one summary line
 * ("Added 3 notes under §2 · 1 suggestion on your text") with Open in notes
 * and Undo, which turns into Apply again; Details folds out what changed.
 *
 * The server is the truth for where the change set stands: the card reads
 * it on mount (so a reloaded chat shows Apply again for an undone set) and
 * follows Apply again to the new set it makes. Undo / Apply again calls go
 * one after another and the control is off while one is in flight, so the
 * card and the server never disagree.
 */
import { ChevronRight, Loader2, NotebookPen, Pencil, Plus, Redo2, Trash2, Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { sectionLabel } from "@/components/notes/blocks/markProps";
import { Collapsible } from "@/components/ui/collapsible";
import { getChangeSet, reapplyChangeSet, undoChangeSet } from "@/lib/api";
import { prefersReducedMotion } from "@/lib/motion";
import { diffText } from "@/lib/notes/textDiff";
import { truncatedTitle } from "@/lib/truncatedTitle";
import { useNotesPaneStore } from "@/store/notesPane";
import { noteMarksChanged, useNotesStore } from "@/store/notes";
import type { NotesChangedData } from "@/types/domain";
import type { ChangeSetChange, ChangeSetStatus } from "@/types/notes";

type Live = { id: string; status: "applied" | "undone" };

/** Reload the note if it is open somewhere, so the editor shows the change. */
function refreshNote(noteId: number) {
  // Its mark blocks may have gone or come back: the reader follows.
  noteMarksChanged(noteId);
  if (useNotesStore.getState().notes[noteId]) {
    void useNotesStore
      .getState()
      .loadNote(noteId)
      .catch(() => undefined);
  }
}

export function NotesChangeCard({ data }: { data: NotesChangedData }) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<ChangeSetStatus | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [busy, setBusy] = useState(false);
  const [kept, setKept] = useState(0);
  const [open, setOpen] = useState(false);
  // Calls run one after another: a click waits for the one before it.
  const chain = useRef<Promise<unknown>>(Promise.resolve());

  useEffect(() => {
    let cancelled = false;
    getChangeSet(data.change_set_id).then(
      (d) => {
        if (cancelled) return;
        setDetail(d);
        setLive((cur) => cur ?? d.head);
      },
      () => {
        // Unknown state: assume the set as the event reported it.
        if (!cancelled) setLive((cur) => cur ?? { id: data.change_set_id, status: "applied" });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [data.change_set_id]);

  const undone = live?.status === "undone";

  /** The Details again (what Undo / Apply again changed: suggestion status,
   * the text of the user's lines). Errors keep the last Details. */
  const refetch = () =>
    getChangeSet(data.change_set_id).then(
      (d) => setDetail(d),
      () => undefined,
    );

  const toggle = () => {
    if (!live || busy) return;
    const target = live;
    setBusy(true);
    chain.current = chain.current
      .then(async () => {
        if (target.status === "applied") {
          const res = await undoChangeSet(target.id);
          // Set on Undo only: the line keeps its text while it folds away.
          setKept(res.rejected.length);
          setLive({ id: target.id, status: "undone" });
        } else {
          const res = await reapplyChangeSet(target.id);
          if (res.change_set_id === null) {
            // Nothing was put back: the change stays undone.
            refreshNote(data.note_id);
            throw new Error("nothing applied again");
          }
          setLive({ id: res.change_set_id, status: "applied" });
        }
        refreshNote(data.note_id);
        await refetch();
      })
      .catch(() => {
        toast.error(t("notes:card.failed", "Couldn't change the note. Try again."));
      })
      .finally(() => setBusy(false));
  };

  const openInNotes = () => {
    const note = detail?.note;
    const first = detail?.changes.find((c) => c.kind !== "deleted")?.block_id ?? null;
    const id = note ? Number(note.target_id) : NaN;
    if (!note || Number.isNaN(id)) {
      useNotesPaneStore.setState({ open: true, activeTab: "notes" });
      return;
    }
    useNotesPaneStore.getState().openAt({ kind: note.target_kind, id, noteId: data.note_id, blockId: first });
  };

  const s = data.summary;
  const parts: { key: string; text: string; amber?: boolean }[] = [];
  // A table is named by its size; the other added blocks are counted.
  const tables = s.tables_added ?? [];
  const added = s.added - tables.length;
  if (added > 0) {
    parts.push({
      key: "added",
      text: s.section
        ? t("notes:card.addedUnder", { count: added, section: sectionLabel(s.section) })
        : t("notes:card.added", { count: added }),
    });
  }
  tables.forEach((table, i) => {
    parts.push({
      key: `table-${i}`,
      text: s.section
        ? t("notes:card.addedTableUnder", "Added a table ({{rows}} rows × {{cols}} columns) under {{section}}", {
            rows: table.rows,
            cols: table.cols,
            section: sectionLabel(s.section),
          })
        : t("notes:card.addedTable", "Added a table ({{rows}} rows × {{cols}} columns)", {
            rows: table.rows,
            cols: table.cols,
          }),
    });
  });
  if (s.updated > 0) {
    parts.push({
      key: "updated",
      text: t("notes:card.updated", { count: s.updated }),
    });
  }
  if (s.deleted > 0) {
    parts.push({
      key: "deleted",
      text: t("notes:card.deleted", { count: s.deleted }),
    });
  }
  if (s.suggested > 0) {
    parts.push({
      key: "suggested",
      amber: true,
      text: t("notes:card.suggested", { count: s.suggested }),
    });
  }
  if (!parts.length) parts.push({ key: "changed", text: t("notes:card.changed", "Changed your notes") });

  const label = undone ? t("notes:card.applyAgain", "Apply again") : t("notes:card.undo", "Undo");
  const ToggleIcon = busy ? Loader2 : undone ? Redo2 : Undo2;

  return (
    <div className="not-prose mt-2 animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none">
      <div
        data-testid="notes-change-card"
        data-undone={undone || undefined}
        className="rounded-xl border border-border bg-card px-2.5 py-2 text-[13px]"
      >
        <div className="flex flex-wrap items-center gap-2">
          <NotebookPen className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-[12rem] flex-1">
            {parts.map((p, i) => (
              <span key={p.key}>
                {i > 0 && " · "}
                <span className={p.amber ? "text-amber-700 dark:text-amber-300" : undefined}>{p.text}</span>
              </span>
            ))}
          </span>
          <button
            type="button"
            onClick={openInNotes}
            disabled={undone || detail === null}
            className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-[12.5px] transition-colors hover:bg-accent disabled:cursor-default disabled:opacity-50"
          >
            {t("notes:card.open", "Open in notes")}
          </button>
          <button
            type="button"
            data-testid="notes-card-toggle"
            onClick={toggle}
            disabled={busy || live === null}
            title={undone ? t("notes:card.applyAgainHint", "Put the agent's changes back") : undefined}
            className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[12.5px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-default disabled:opacity-50"
          >
            <ToggleIcon className={busy ? "size-3.5 animate-spin" : "size-3.5"} aria-hidden />
            {label}
          </button>
        </div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`notes-card-details-${data.change_set_id}`}
          onClick={() => setOpen((o) => !o)}
          className="mt-0.5 inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <ChevronRight
            className={`size-3 transition-transform duration-200 ease-out motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
            aria-hidden
          />
          {t("notes:card.details", "Details")}
        </button>
        <div id={`notes-card-details-${data.change_set_id}`} data-testid="notes-card-details" data-state={open ? "open" : "closed"}>
          <Collapsible
            open={open}
            onExpanded={(el) => el.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" })}
          >
            <ChangeList changes={detail?.changes ?? null} />
          </Collapsible>
        </div>
      </div>
      <Collapsible open={undone}>
        <p className="mt-1.5 flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
          <Undo2 className="size-3.5 shrink-0" aria-hidden />
          {t("notes:card.undone", "Undone: the agent's changes were removed. Your own text was not touched.")}
          {kept > 0 &&
            " " +
              t("notes:card.kept", {
                count: kept })}
        </p>
      </Collapsible>
    </div>
  );
}

function ChangeList({ changes }: { changes: ChangeSetChange[] | null }) {
  const { t } = useTranslation();
  if (changes === null) {
    return <p className="px-1 py-1 text-xs text-muted-foreground">{t("notes:loading", "Loading the note…")}</p>;
  }
  if (!changes.length) {
    return <p className="px-1 py-1 text-xs text-muted-foreground">{t("notes:card.nothing", "Nothing left to show.")}</p>;
  }
  return (
    <ul className="my-1 flex flex-col gap-1 text-[12.5px]">
      {changes.map((c) => (
        <li key={`${c.kind}:${c.block_id}`} className="grid grid-cols-[16px_minmax(0,1fr)] items-start gap-1.5">
          {c.kind === "suggested" ? (
            <Pencil className="mt-0.5 size-3.5 text-amber-600 dark:text-amber-300" aria-hidden />
          ) : c.kind === "deleted" ? (
            <Trash2 className="mt-0.5 size-3.5 text-muted-foreground" aria-hidden />
          ) : (
            <Plus className="mt-0.5 size-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden />
          )}
          <ChangeLine change={c} />
        </li>
      ))}
    </ul>
  );
}

function ChangeLine({ change: c }: { change: ChangeSetChange }) {
  const { t } = useTranslation();
  if (c.kind === "added") return <span {...truncatedTitle(c.text)}>{c.text}</span>;
  if (c.kind === "deleted") {
    return (
      <span>
        {t("notes:card.removedLine", "Removed:")} <del className="text-muted-foreground">{c.text}</del>
      </span>
    );
  }
  const d = diffText(c.before ?? "", c.op === "delete" ? "" : c.text);
  const lead = c.kind === "suggested" ? t("notes:card.suggestedLine", "Suggested on your line:") : t("notes:card.updatedLine", "Updated:");
  const settled =
    c.kind === "suggested" && c.status && c.status !== "open"
      ? {
          accepted: t("notes:card.status.accepted", "accepted"),
          rejected: t("notes:card.status.rejected", "rejected"),
          withdrawn: t("notes:card.status.withdrawn", "withdrawn"),
        }[c.status]
      : null;
  return (
    <span>
      {lead}{" "}
      {d.del && <del className="text-muted-foreground decoration-rose-500">{d.del}</del>}
      {d.del && d.ins && " "}
      {d.ins && (
        <ins className="border-b border-emerald-600 bg-emerald-500/15 no-underline dark:border-emerald-400">{d.ins}</ins>
      )}
      {settled && <span className="ml-1 text-xs text-muted-foreground">({settled})</span>}
    </span>
  );
}
