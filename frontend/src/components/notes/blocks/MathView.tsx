import katex from "katex";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import "katex/dist/katex.min.css";

/** A display equation: rendered with KaTeX, click to edit its LaTeX. */
export function MathView({ latex, onChange }: { latex: string; onChange: (latex: string) => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(latex === "");
  const html = useMemo(
    () => (latex ? katex.renderToString(latex, { displayMode: true, throwOnError: false }) : ""),
    [latex],
  );
  return (
    <div contentEditable={false} className="w-full">
      <button
        type="button"
        className="block w-full overflow-x-auto rounded-md px-2 py-1 text-left transition-colors hover:bg-muted"
        title={t("notes:math.edit", "Click to edit the LaTeX")}
        onClick={() => setEditing((e) => !e)}
      >
        {html ? (
          <span dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <span className="text-sm text-muted-foreground">{t("notes:math.empty", "Empty equation")}</span>
        )}
      </button>
      <div
        data-state={editing ? "open" : "closed"}
        className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <div className="min-h-0 overflow-hidden">
          <textarea
            aria-label={t("notes:math.latex", "LaTeX")}
            value={latex}
            rows={2}
            spellCheck={false}
            placeholder="E = mc^2"
            className="mt-1 w-full resize-y rounded-md border border-border bg-background px-2 py-1 font-mono text-xs"
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) {
                e.preventDefault();
                setEditing(false);
              }
            }}
          />
        </div>
      </div>
    </div>
  );
}
