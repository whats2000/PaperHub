import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import "katex/dist/katex.min.css";

import { renderDisplayMath } from "@/lib/displayMath";

/** Where editing ended: below the equation, above it, or on it (Escape). */
export type MathLeave = "down" | "up" | "stay";

/** A display equation: while editing, its LaTeX in a monospace field with
 * the rendered formula live under it; otherwise the formula, which a click
 * edits again. Enter (or Ctrl+Enter) finishes, Shift+Enter is a new line,
 * Escape / a click outside / an arrow past the first or last line leave. */
export function MathView({
  latex,
  onChange,
  isNew,
  onLeave,
}: {
  latex: string;
  onChange: (latex: string) => void;
  /** Read once on mount: the block was just made here, so the caret goes to its LaTeX. */
  isNew?: () => boolean;
  /** Editing ended with a key: where the editor's caret goes. */
  onLeave?: (to: MathLeave) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(latex === "");
  const field = useRef<HTMLTextAreaElement>(null);
  // Focus follows a click on the formula, never an equation merely shown.
  const focusOnOpen = useRef(false);
  useEffect(() => {
    if (isNew?.()) field.current?.focus();
    // Only the first render: a later empty LaTeX is the user clearing it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (editing && focusOnOpen.current) field.current?.focus();
    focusOnOpen.current = false;
  }, [editing]);
  const html = useMemo(() => renderDisplayMath(latex), [latex]);

  const leave = (to: MathLeave) => {
    // An empty equation keeps its field: there is nothing else to show.
    if (latex !== "") setEditing(false);
    onLeave?.(to);
  };

  return (
    <div contentEditable={false} className="w-full">
      <div
        data-state={editing ? "open" : "closed"}
        className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <div className="min-h-0 overflow-hidden">
          <textarea
            ref={field}
            aria-label={t("notes:math.latex", "LaTeX")}
            title={t("notes:math.keys", "Enter: done · Shift+Enter: new line · Esc")}
            tabIndex={editing ? 0 : -1}
            value={latex}
            rows={Math.min(8, Math.max(2, latex.split("\n").length))}
            spellCheck={false}
            placeholder="E = mc^2"
            className="mb-1 w-full resize-y rounded-md border border-border bg-background px-2 py-1 font-mono text-xs"
            onChange={(e) => onChange(e.target.value)}
            onBlur={() => {
              if (latex !== "") setEditing(false);
            }}
            onKeyDown={(e) => {
              const el = e.currentTarget;
              const { value, selectionStart: s, selectionEnd: end } = el;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                leave("down");
              } else if (e.key === "Escape") {
                e.preventDefault();
                leave("stay");
              } else if (e.key === "ArrowUp" && s === end && value.lastIndexOf("\n", s - 1) === -1) {
                e.preventDefault();
                leave("up");
              } else if (e.key === "ArrowDown" && s === end && value.indexOf("\n", end) === -1) {
                e.preventDefault();
                leave("down");
              }
            }}
          />
        </div>
      </div>
      <button
        type="button"
        data-math-render=""
        className="block w-full overflow-x-auto rounded-md px-2 py-1 text-left transition-colors hover:bg-muted"
        title={t("notes:math.edit", "Click to edit the LaTeX")}
        // While editing the formula is the live preview: a click on it keeps
        // the field focused instead of closing it.
        onMouseDown={(e) => {
          if (editing) e.preventDefault();
        }}
        onClick={() => {
          focusOnOpen.current = true;
          setEditing(true);
          if (editing) field.current?.focus();
        }}
      >
        {html ? (
          <span dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <span className="text-sm text-muted-foreground">{t("notes:math.empty", "Empty equation")}</span>
        )}
      </button>
    </div>
  );
}
