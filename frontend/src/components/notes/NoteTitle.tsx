/** The note's page title (Notion-style): a large editable line at the top of
 * the notes pane. Empty, it shows the default title (the paper / chat /
 * idea's title, else "Untitled") as a grey placeholder only: the default
 * is never written into the field or saved. Saved on a pause in typing, on blur and on Enter. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { defaultNoteTitle } from "@/lib/notes/title";
import { useNotesStore } from "@/store/notes";

/** How long a pause in typing waits before the title is saved. */
export const TITLE_SAVE_DELAY_MS = 800;

interface Props {
  noteId: number;
  /** Enter, or ArrowDown at the end: the caret goes to the first block. */
  onExitDown: () => void;
}

export function NoteTitle({ noteId, onExitDown }: Props) {
  const { t } = useTranslation("notes");
  const saved = useNotesStore((s) => s.notes[noteId]?.note.title ?? "");
  const placeholder = useNotesStore((s) => defaultNoteTitle(s.notes[noteId]));
  const renameNote = useNotesStore((s) => s.renameNote);
  const [draft, setDraft] = useState(saved);
  const [editing, setEditing] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // A title changed elsewhere (another editor, a reload) shows here unless
  // the user is typing in this one.
  const [seen, setSeen] = useState(saved);
  if (saved !== seen) {
    setSeen(saved);
    if (!editing) setDraft(saved);
  }

  const save = useCallback(
    (value: string) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      const next = value.replace(/\s+/g, " ").trim();
      if (next === useNotesStore.getState().notes[noteId]?.note.title) return;
      renameNote(noteId, next).catch(() => {
        /* the store toasts and puts the old title back */
      });
    },
    [noteId, renameNote],
  );

  // Typed but not yet saved when the editor goes away: save it then.
  const latest = useRef(draft);
  useEffect(() => {
    latest.current = draft;
  }, [draft]);
  useEffect(
    () => () => {
      if (timer.current) save(latest.current);
    },
    [save],
  );

  // A typed title wraps and the field grows with it (pushing the note
  // down), on typing and when the pane's width changes. Empty, it is one line.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    let width = el.clientWidth;
    const ro = new ResizeObserver(() => {
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        fit();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [draft]);

  const empty = draft === "";
  return (
    <div className="relative">
      {/* The default title: one line, cut with an ellipsis, never growing the
          field (a textarea's own placeholder would wrap). */}
      {empty && (
        <div
          aria-hidden
          data-testid="note-title-placeholder"
          className="pointer-events-none absolute inset-x-0 top-0 truncate px-[54px] pt-4 pb-2 text-[1.75rem] leading-tight font-bold text-muted-foreground/50"
        >
          {placeholder}
        </div>
      )}
      <textarea
        ref={ref}
        rows={1}
        data-testid="note-title"
        aria-label={t("title.label", "Note title")}
        aria-placeholder={placeholder}
        value={draft}
        spellCheck={false}
        onFocus={() => setEditing(true)}
        onBlur={() => {
          setEditing(false);
          save(draft);
        }}
        onChange={(e) => {
          // A title is one line of text: a pasted line break becomes a space.
          const value = e.target.value.replace(/[\r\n]+/g, " ");
          setDraft(value);
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => save(value), TITLE_SAVE_DELAY_MS);
        }}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          const el = e.currentTarget;
          const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length;
          if (e.key === "Enter" || (e.key === "ArrowDown" && atEnd)) {
            e.preventDefault();
            save(draft);
            onExitDown();
          }
        }}
        className="relative block w-full resize-none overflow-hidden bg-transparent px-[54px] pt-4 pb-2 text-[1.75rem] leading-tight font-bold break-words whitespace-pre-wrap text-foreground outline-none"
      />
    </div>
  );
}
