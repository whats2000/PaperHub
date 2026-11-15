import { Check, ChevronDown, Copy, Database } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Collapsible } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/** A library_stats answer ends with the executed query in a ```sql block.
 *  Rather than letting it render as a raw code block folded into the prose,
 *  MessageBubble's markdown `pre` override hands sql blocks here — a compact,
 *  collapsible, copyable card shown distinctly below the answer text. The
 *  query folds up into its header and opens back down from it. */
export function SqlCard({ sql }: { sql: string }) {
  const { t } = useTranslation("chat");
  const [copied, setCopied] = useState(false);
  const [open, setOpen] = useState(true);

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
    <div className="not-prose my-2 overflow-hidden rounded-lg border border-border bg-muted/30">
      <div className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-muted-foreground">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex flex-1 items-center gap-1.5 text-left hover:text-foreground"
        >
          <Database className="size-3.5" aria-hidden="true" />
          {/* "SQL" is a universal technical term — not localized. */}
          <span>SQL</span>
          <ChevronDown
            aria-hidden="true"
            className={cn(
              "size-3.5 transition-transform duration-200 ease-out motion-reduce:transition-none",
              !open && "-rotate-90",
            )}
          />
        </button>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center rounded p-1 hover:bg-accent hover:text-foreground"
          aria-label={t("bubble.copy")}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
        </button>
      </div>
      <Collapsible open={open}>
        <pre className="overflow-x-auto border-t border-border bg-background/50 px-3 py-2 font-mono text-xs leading-relaxed text-foreground">
          {sql}
        </pre>
      </Collapsible>
    </div>
  );
}
