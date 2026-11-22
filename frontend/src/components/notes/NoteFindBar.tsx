/** The find bar of a notes editor (Ctrl+F / Cmd+F, or the toolbar's Find).
 *
 * In the notes pane it is a full-width strip directly under the toolbar
 * (portalled into the pane's find host); in the note card it is the same
 * strip, compact, at the top of the card's editor. It grows down (height)
 * and shrinks back up on close.
 *
 * Typing finds as you go; Enter / F3 goes to the next match, Shift+Enter /
 * Shift+F3 to the previous, both wrapping. The current match is selected in
 * the editor (a reference gets the atom caret), scrolled into view, and its
 * folded part unfolds. Esc closes the bar with the editor's caret on the
 * current match (or back where it was when nothing matched).
 */
import { NodeSelection, Selection, TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { useNotesStore } from "@/store/notes";

import { setAtomCaret } from "./atomCaret";
import { findMatches, setFindMatches, type FindMatch } from "./noteFind";
import { foldersHiding, type FoldNode } from "./folds";
import { foldedRanges } from "./partsDecorations";
import { partRequests } from "./tableCaptions";

interface FindEditor {
  _tiptapEditor: { view: EditorView };
  document: unknown[];
  onChange: (cb: () => void) => (() => void) | void;
}

export interface NoteFindBarProps {
  editor: FindEditor;
  noteId: number;
  /** The note card's one part (null: the whole note shows). */
  cardPart: string | null;
  /** Bumped on each Ctrl+F: the field takes focus with its text selected. */
  focusSeq: number;
  /** False: the bar slides out, then `onGone`. */
  open: boolean;
  onClose: () => void;
  onGone: () => void;
  /** The pane's strip under its toolbar; none: at the top of the editor. */
  host?: HTMLElement | null;
  /** The note card's smaller strip. */
  compact?: boolean;
}

export function NoteFindBar({
  editor,
  noteId,
  cardPart,
  focusSeq,
  open,
  onClose,
  onGone,
  host = null,
  compact = false,
}: NoteFindBarProps) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<FindMatch[]>([]);
  const [current, setCurrent] = useState(-1);
  const view = useCallback(() => editor._tiptapEditor.view, [editor]);
  // Where the caret was when the bar opened: the first match is the next one
  // from there, and Esc with no match puts it back.
  const [startSel] = useState(() => editor._tiptapEditor.view.state.selection);

  /** The matches of `q` in what this editor shows. */
  const compute = useCallback(
    (q: string): FindMatch[] => {
      const doc = view().state.doc;
      const all = findMatches(doc, q);
      if (cardPart === null) return all;
      const hidden = foldedRanges(doc, { focusOn: false, activePart: null, folded: new Set(), only: cardPart });
      return all.filter((m) => !hidden.some(([a, b]) => m.from >= a && m.from < b));
    },
    [view, cardPart],
  );

  /** Makes match `i` the current one: unfolded, selected, in view. */
  const show = useCallback(
    (list: FindMatch[], i: number) => {
      const v = view();
      const m = list[i];
      if (m && cardPart === null) {
        const folded = useNotesStore.getState().folded[noteId] ?? [];
        const hiding = foldersHiding(editor.document as unknown as FoldNode[], m.blockId, new Set(folded));
        if (hiding.length) useNotesStore.getState().setFolded(noteId, folded.filter((id) => !hiding.includes(id)));
      }
      setFindMatches(v, list, m ? i : -1);
      if (!m) return;
      const inPart = m.kind === "caption" || m.kind === "notes";
      if (m.kind === "block") setAtomCaret(v, m.from, "before");
      else if (!inPart) {
        const sel = m.kind === "node" ? NodeSelection.create(v.state.doc, m.from) : TextSelection.create(v.state.doc, m.from, m.to);
        v.dispatch(v.state.tr.setSelection(sel));
      }
      // A table's caption / notes draws its own highlight after this render:
      // the part itself is scrolled to (the caret stays where it was). The
      // scroll waits a frame: Chrome cancels a smooth scroll started inside
      // the find field's typing event, so typing never brought a match into
      // view (only Enter did).
      requestAnimationFrame(() => {
        const el = inPart
          ? v.dom.querySelector(`[data-id="${m.blockId}"] [data-table-caption="${m.kind}"]`)
          : v.dom.querySelector(".ph-find-current");
        if (el && typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
        }
      });
    },
    [view, editor, noteId, cardPart],
  );

  const run = useCallback(
    (q: string) => {
      const list = compute(q);
      const from = startSel.from;
      const i = list.length ? Math.max(0, list.findIndex((m) => m.to > from)) : -1;
      setMatches(list);
      setCurrent(i);
      show(list, i);
    },
    [compute, show, startSel],
  );

  const step = useCallback(
    (dir: 1 | -1) => {
      if (!matches.length) return;
      const i = (current + dir + matches.length) % matches.length;
      setCurrent(i);
      show(matches, i);
    },
    [matches, current, show],
  );

  // Edits while the bar is open: the matches follow, the current one kept
  // (by place) without moving the caret.
  const live = useRef({ query, current, matches });
  useEffect(() => {
    live.current = { query, current, matches };
  });
  useEffect(() => {
    const off = editor.onChange(() => {
      const { query: q, current: c, matches: old } = live.current;
      if (!q) return;
      const list = compute(q);
      const at = old[c]?.from ?? 0;
      const i = list.length ? Math.max(0, list.findIndex((m) => m.to > at)) : -1;
      setMatches(list);
      setCurrent(i);
      setFindMatches(view(), list, i);
    });
    return () => {
      if (typeof off === "function") off();
    };
  }, [editor, compute, view]);

  // Leaving: the highlights go with the bar.
  useEffect(
    () => () => {
      const v = view();
      if (!v.isDestroyed) setFindMatches(v, [], -1);
    },
    [view],
  );

  useLayoutEffect(() => {
    if (!open) return;
    input.current?.focus();
    input.current?.select();
  }, [focusSeq, open]);

  // Closed: gone once the slide out ends (at once under reduced motion).
  // A native listener: React's onAnimationEnd is vendor-guessed.
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) return;
    if (prefersReducedMotion()) {
      onGone();
      return;
    }
    const el = panel.current;
    el?.addEventListener("animationend", onGone);
    return () => el?.removeEventListener("animationend", onGone);
  }, [open, onGone]);

  const close = () => {
    const v = view();
    setFindMatches(v, [], -1);
    if (current < 0) {
      try {
        v.dispatch(v.state.tr.setSelection(Selection.fromJSON(v.state.doc, startSel.toJSON())));
      } catch {
        /* the old place is gone: the caret stays */
      }
    }
    // On a match in a table's caption or notes: that part opens with the
    // match selected, so the next key edits there.
    const m = current >= 0 ? matches[current] : undefined;
    if ((m?.kind === "caption" || m?.kind === "notes") && m.at) {
      partRequests(editor).request(m.blockId, m.kind, m.at);
    } else v.focus();
    onClose();
  };

  const total = matches.length;
  const iconButton = "inline-flex shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40 motion-reduce:transition-none";
  const bar = (
    <div
      role="search"
      data-testid="note-find"
      data-state={open ? "open" : "closed"}
      data-variant={compact ? "card" : "pane"}
      ref={panel}
      className={cn(
        // A full-width strip; the height is what animates (note-find-in/out).
        "w-full shrink-0 overflow-hidden border-b border-border motion-reduce:animate-none",
        host ? "bg-card" : "sticky top-0 z-20 bg-[var(--sticky-bg,var(--card))]",
        compact ? "h-8" : "h-9",
        open ? "note-find-in" : "note-find-out",
      )}
    >
      <div className={cn("flex h-full items-center", compact ? "gap-1 px-2" : "gap-1.5 px-2.5")}>
        <Search className={cn("shrink-0 text-muted-foreground", compact ? "size-3" : "size-3.5")} aria-hidden />
        <input
          ref={input}
          value={query}
          aria-label={t("notes:find.label", "Find in the note")}
          placeholder={t("notes:find.placeholder", "Find in the note")}
          className={cn(
            "h-full min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground",
            compact ? "text-[12.5px]" : "text-sm",
          )}
          onChange={(e) => {
            setQuery(e.target.value);
            run(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              close();
            } else if (e.key === "Enter" || e.key === "F3") {
              e.preventDefault();
              step(e.shiftKey ? -1 : 1);
            }
          }}
        />
        <span
          data-testid="note-find-count"
          className="shrink-0 px-1 text-xs whitespace-nowrap tabular-nums text-muted-foreground"
        >
          {t("notes:find.count", "{{current}} / {{total}}", { current: total ? current + 1 : 0, total })}
        </span>
        <button
          type="button"
          aria-label={t("notes:find.prev", "Previous match")}
          title={t("notes:find.prev", "Previous match")}
          disabled={!total}
          onClick={() => step(-1)}
          className={cn(iconButton, compact ? "size-5" : "size-6")}
        >
          <ChevronUp className="size-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("notes:find.next", "Next match")}
          title={t("notes:find.next", "Next match")}
          disabled={!total}
          onClick={() => step(1)}
          className={cn(iconButton, compact ? "size-5" : "size-6")}
        >
          <ChevronDown className="size-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("notes:find.close", "Close find")}
          title={t("notes:find.close", "Close find")}
          onClick={close}
          className={cn(iconButton, compact ? "size-5" : "size-6")}
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
  return host ? createPortal(bar, host) : bar;
}
