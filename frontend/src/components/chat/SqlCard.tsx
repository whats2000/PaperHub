import { Check, Copy, Database } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

/** A library_stats answer ends with the executed query in a ```sql block.
 *  Rather than letting it render as a raw code block folded into the prose,
 *  MessageBubble's markdown `pre` override hands sql blocks here — a compact,
 *  collapsible, copyable card shown distinctly below the answer text. */
export function SqlCard({ sql }: { sql: string }) {
  const { t } = useTranslation("chat");
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(sql).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => toast.error(t("toast.copyFailed")),
    );
  };

  return (
    <details
      open
      className="not-prose group my-2 overflow-hidden rounded-lg border border-border bg-muted/30"
    >
      <summary className="flex cursor-pointer select-none list-none items-center gap-1.5 px-3 py-2 text-xs font-medium text-muted-foreground [&::-webkit-details-marker]:hidden">
        <Database className="size-3.5" />
        {/* "SQL" is a universal technical term — not localized. */}
        <span>SQL</span>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            copy();
          }}
          className="ml-auto inline-flex items-center rounded p-1 hover:bg-accent hover:text-foreground"
          aria-label={t("bubble.copy")}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
        </button>
      </summary>
      <pre className="overflow-x-auto border-t border-border bg-background/50 px-3 py-2 font-mono text-xs leading-relaxed text-foreground">
        {sql}
      </pre>
    </details>
  );
}
