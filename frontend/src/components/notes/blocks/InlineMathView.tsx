import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import "katex/dist/katex.min.css";

import { renderInlineMath } from "@/lib/displayMath";
import { cn } from "@/lib/utils";

/** The LaTeX field and live preview of an inline equation. The LaTeX is a
 * draft while editing and is saved when editing ends. */
export function InlineMathView({
  latex,
  autoEdit = false,
  onCommit,
  onLeave,
  onMounted,
}: {
  latex: string;
  autoEdit?: boolean;
  /** Editing ended with this LaTeX ("" removes the equation). */
  onCommit: (latex: string) => void;
  /** Where the editor's caret goes after editing ends with a key. */
  onLeave?: (side: "before" | "after") => void;
  onMounted?: () => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(autoEdit);
  const [draft, setDraft] = useState(latex);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    onMounted?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (editing) field.current?.focus();
  }, [editing]);
  const html = useMemo(() => renderInlineMath(editing ? draft : latex), [editing, draft, latex]);

  const finish = (side?: "before" | "after") => {
    if (!editing) return;
    setEditing(false);
    if (draft !== latex || draft.trim() === "") onCommit(draft.trim() === "" ? "" : draft);
    if (side) onLeave?.(side);
  };

  return (
    <span contentEditable={false} className="inline-flex items-baseline align-baseline" data-inline-math="">
      <span
        data-state={editing ? "open" : "closed"}
        className="inline-block max-w-[40ch] overflow-hidden whitespace-nowrap align-baseline transition-[max-width,opacity] duration-200 ease-out data-[state=closed]:max-w-0 data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <input
          ref={field}
          aria-label={t("notes:math.latex", "LaTeX")}
          title={t("notes:math.keys", "Enter: done · Shift+Enter: new line · Esc")}
          tabIndex={editing ? 0 : -1}
          value={draft}
          spellCheck={false}
          size={Math.max(4, draft.length + 1)}
          placeholder="x^2"
          className="mr-1 rounded border border-border bg-background px-1 font-mono text-xs"
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
      <button
        type="button"
        data-inline-math-render=""
        title={t("notes:math.edit", "Click to edit the LaTeX")}
        className={cn(
          "rounded px-0.5 align-baseline transition-colors hover:bg-muted",
          editing && "bg-muted",
        )}
        // Keep the field focused while editing: a click here must not blur it.
        onMouseDown={(e) => {
          if (editing) e.preventDefault();
        }}
        onClick={() => {
          if (!editing) {
            setDraft(latex);
            setEditing(true);
          }
        }}
      >
        {html ? (
          <span dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <span className="text-xs text-muted-foreground">{t("notes:math.emptyInline", "Empty math")}</span>
        )}
      </button>
    </span>
  );
}
