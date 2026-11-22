/** A table's caption and notes, as in a journal: "Table N." and the caption
 * above the table, the notes in small muted text below it (abbreviations,
 * "* p < 0.05"; several lines). Both are the table block's props (plain
 * text, `$…$` drawn as inline math like `inlineMath`), edited in place; an
 * emptied one is removed. N counts the note's captioned tables in order.
 *
 * Each part renders into its host above / below the table (tableCaptions.ts).
 * A new part opens downward into place; a removed one collapses toward the
 * table (the caption sinks into it, the notes rise into it).
 */
import { useBlockNoteEditor, useEditorState } from "@blocknote/react";
import { Fragment, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import "katex/dist/katex.min.css";

import { renderInlineMath } from "@/lib/displayMath";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { noteFindKey, type TablePart } from "./noteFind";
import { partRequests, tablesOf, type CaptionHosts, type PartRequest } from "./tableCaptions";

const EXIT_MS = 200;
/** `$…$` math (an escaped `\$` is a dollar sign), as the export reads it. */
const MATH = /(?<!\\)\$((?:\\\$|[^$])+?)\$/g;

interface Hit {
  at: [number, number];
  current: boolean;
}

const hitClass = (h: Hit) => (h.current ? "ph-find ph-find-current" : "ph-find");

/** `text` with its `$…$` drawn by KaTeX and the find hits marked (a hit
 * touching an equation marks the whole equation). */
function PartText({ text, hits }: { text: string; hits: Hit[] }) {
  const pieces: React.ReactNode[] = [];
  const plain = (from: number, to: number) => {
    // Cut at every hit edge inside [from, to).
    const cuts = new Set([from, to]);
    for (const h of hits) for (const e of h.at) if (e > from && e < to) cuts.add(e);
    const edges = [...cuts].sort((a, b) => a - b);
    for (let i = 0; i + 1 < edges.length; i++) {
      const [a, b] = [edges[i]!, edges[i + 1]!];
      const hit = hits.find((h) => h.at[0] <= a && b <= h.at[1]);
      const s = text.slice(a, b);
      pieces.push(hit ? <span key={`t${a}`} className={hitClass(hit)}>{s}</span> : <Fragment key={`t${a}`}>{s}</Fragment>);
    }
  };
  let last = 0;
  for (const m of text.matchAll(MATH)) {
    const start = m.index;
    const end = start + m[0].length;
    if (start > last) plain(last, start);
    const hit = hits.find((h) => h.at[0] < end && h.at[1] > start);
    pieces.push(
      <span
        key={`m${start}`}
        className={hit ? hitClass(hit) : undefined}
        dangerouslySetInnerHTML={{ __html: renderInlineMath(m[1]!) }}
      />,
    );
    last = end;
  }
  if (last < text.length) plain(last, text.length);
  return <>{pieces}</>;
}

interface PartProps {
  part: TablePart;
  value: string;
  /** The caption's N (0 for the notes). */
  number: number;
  hits: Hit[];
  /** A request to open this part (the table menu, or find closing on a
   * match in it): its number, and what to select. */
  request: number;
  select?: [number, number];
  onCommit: (value: string) => void;
  onEditing: (editing: boolean) => void;
  onLeave: () => void;
}

/** One part (caption or notes) of one table. */
function TablePartView({ part, value, number, hits, request, select, onCommit, onEditing, onLeave }: PartProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const field = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const shown = editing || value !== "";

  // What a closing part keeps showing while it collapses.
  const [ghost, setGhost] = useState<{ value: string; number: number } | null>(null);
  // (State from the last render, React's "previous props" pattern.)
  const now = { shown, value: editing ? draft : value, number };
  const [prev, setPrev] = useState(now);
  if (prev.shown !== now.shown || (shown && (prev.value !== now.value || prev.number !== number))) {
    setPrev(now);
    if (shown) {
      if (ghost) setGhost(null);
    } else if (!prefersReducedMotion()) {
      setGhost({ value: prev.value, number: prev.number });
    }
  }
  useEffect(() => {
    if (!ghost) return;
    const timer = setTimeout(() => setGhost(null), EXIT_MS);
    return () => clearTimeout(timer);
  }, [ghost]);

  const open = () => {
    setDraft(value);
    setEditing(true);
  };

  // A request ("Add caption" / "Add notes", or find closing on a match
  // here): opened with the caret in it, or the match selected. Taken in
  // render (React's "previous props" pattern).
  const [seen, setSeen] = useState(request);
  const [focusAsk, setFocusAsk] = useState<{ n: number; select: [number, number] | null }>({ n: 0, select: null });
  if (request !== 0 && request !== seen) {
    setSeen(request);
    setFocusAsk((f) => ({ n: f.n + 1, select: select ?? null }));
    if (!editing) {
      setDraft(value);
      setEditing(true);
    }
  }
  // The parent counts a caption being written in the numbering.
  const reported = useRef(editing);
  useEffect(() => {
    if (reported.current === editing) return;
    reported.current = editing;
    onEditing(editing);
  }, [editing, onEditing]);
  useEffect(() => {
    if (!editing) return;
    // After the menu (or the find bar) that opened it has closed and
    // handed the focus back.
    const timer = setTimeout(() => {
      const el = field.current;
      if (!el) return;
      el.focus();
      if (focusAsk.select) el.setSelectionRange(focusAsk.select[0], focusAsk.select[1]);
    }, 0);
    return () => clearTimeout(timer);
  }, [editing, focusAsk]);

  const finish = (leave: boolean) => {
    if (!editing) return;
    setEditing(false);
    const next = draft.trim();
    if (next !== value) onCommit(next);
    if (leave) onLeave();
  };

  // A closing part keeps its last words while it collapses.
  const closing = !shown && ghost !== null;
  const shownValue = shown ? value : (ghost?.value ?? "");
  const label =
    part === "caption" ? (
      <span className="font-semibold">
        {t("notes:table.captionLabel", "Table {{n}}.", { n: shown ? number : (ghost?.number ?? number) })}
      </span>
    ) : null;

  let body: React.ReactNode = null;
  if (editing) {
    body =
      part === "caption" ? (
        <div className="flex items-baseline gap-1.5 py-0.5 text-sm">
          {label}
          <input
            ref={field}
            aria-label={t("notes:table.captionField", "Table caption")}
            title={t("notes:table.captionKeys", "Enter: done · Esc")}
            value={draft}
            placeholder={t("notes:table.captionPlaceholder", "Caption — $math$ works")}
            spellCheck
            className="min-w-0 flex-1 rounded border border-border bg-background px-1 text-sm outline-none focus-visible:ring-1 focus-visible:ring-ring"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => finish(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === "Escape") {
                e.preventDefault();
                finish(true);
              }
            }}
          />
        </div>
      ) : (
        <textarea
          ref={field}
          aria-label={t("notes:table.notesField", "Table notes")}
          title={t("notes:table.notesKeys", "Enter: new line · Ctrl+Enter or Esc: done")}
          value={draft}
          rows={Math.max(1, draft.split("\n").length)}
          placeholder={t("notes:table.notesPlaceholder", "Notes — abbreviations, * p < 0.05")}
          className="block w-full resize-none rounded border border-border bg-background px-1 py-0.5 text-xs text-muted-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => finish(false)}
          onKeyDown={(e) => {
            if (e.key === "Escape" || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) {
              e.preventDefault();
              finish(true);
            }
          }}
        />
      );
  } else if (shown || (closing && (label || shownValue))) {
    body = (
      <button
        type="button"
        title={t("notes:table.editPart", "Click to edit")}
        disabled={!shown}
        onClick={open}
        className={cn(
          "block w-full cursor-text rounded px-0.5 text-left whitespace-pre-wrap transition-colors hover:bg-muted/60",
          part === "caption" ? "py-0.5 text-sm" : "py-0.5 text-xs leading-snug text-muted-foreground",
        )}
      >
        {label}
        {label && " "}
        <PartText text={shownValue} hits={shown ? hits : []} />
      </button>
    );
  }

  return (
    <div
      data-table-caption={part}
      data-side={part === "caption" ? "above" : "below"}
      data-state={shown ? "open" : "closed"}
      className={cn(
        "grid overflow-hidden transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none",
        "data-[state=open]:grid-rows-[1fr] data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0",
        // A caption opens downward into place and sinks into the table as
        // it goes; notes open and close at their bottom edge.
        part === "caption" ? "data-[state=open]:items-start data-[state=closed]:items-end" : "items-start",
      )}
    >
      <div className="min-h-0 overflow-hidden">{body}</div>
    </div>
  );
}

/** Every table's caption and notes, rendered into their hosts. Rendered
 * inside `BlockNoteView`. */
export function TableCaptions({ hosts }: { hosts: CaptionHosts }) {
  const editor = useBlockNoteEditor();
  const tables = useEditorState({ editor, selector: ({ editor: e }) => tablesOf(e.prosemirrorState.doc) });
  const hits = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const s = noteFindKey.getState(e.prosemirrorState);
      const out: Record<string, Hit[]> = {};
      s?.matches.forEach((m, i) => {
        if ((m.kind !== "caption" && m.kind !== "notes") || !m.at) return;
        (out[`${m.blockId}:${m.kind}`] ??= []).push({ at: m.at, current: i === s.current });
      });
      return out;
    },
  });
  const requests = partRequests(editor);
  const request = useSyncExternalStore(requests.subscribe, requests.get);
  // Captions being written (still empty) count in the numbering.
  const [adding, setAdding] = useState<ReadonlySet<string>>(new Set());

  const numbers = useMemo(() => {
    let n = 0;
    return new Map(tables.map((tb) => [tb.id, tb.caption || adding.has(tb.id) ? ++n : 0]));
  }, [tables, adding]);

  const commit = (id: string, part: TablePart) => (value: string) => {
    editor.updateBlock(id, { props: { [part]: value } });
  };
  const editingOf = (id: string, part: TablePart) => (on: boolean) => {
    if (part !== "caption") return;
    setAdding((prev) => {
      if (prev.has(id) === on) return prev;
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };
  const seqOf = (req: PartRequest | null, id: string, part: TablePart) =>
    req && req.blockId === id && req.part === part ? req.seq : 0;

  return (
    <>
      {tables.map((tb) => (
        <Fragment key={tb.id}>
          {(["caption", "notes"] as const).map((part) =>
            createPortal(
              <TablePartView
                part={part}
                value={tb[part]}
                number={part === "caption" ? (numbers.get(tb.id) ?? 0) : 0}
                hits={hits?.[`${tb.id}:${part}`] ?? []}
                request={seqOf(request, tb.id, part)}
                select={seqOf(request, tb.id, part) ? request?.select : undefined}
                onCommit={commit(tb.id, part)}
                onEditing={editingOf(tb.id, part)}
                onLeave={() => editor.focus()}
              />,
              hosts.host(tb.id, part),
              part,
            ),
          )}
        </Fragment>
      ))}
    </>
  );
}
