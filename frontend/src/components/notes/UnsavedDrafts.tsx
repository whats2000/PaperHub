/** New blocks the server would not take, kept on this screen as unsaved
 * drafts: each is marked "Not saved" in the editor and listed above it with
 * a Copy action. Editing one tries to save it once more (the store). */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, CloudOff, Copy } from "lucide-react";
import { toast } from "sonner";

import { prefersReducedMotion } from "@/lib/motion";
import { blockText } from "@/lib/notes/ops";
import { truncatedTitle } from "@/lib/truncatedTitle";
import { useNotesStore } from "@/store/notes";

const NO_DRAFTS = {};

function cssId(id: string): string {
  return typeof CSS !== "undefined" && typeof CSS.escape === "function"
    ? CSS.escape(id)
    : id.replace(/["\\]/g, "\\$&");
}

export function UnsavedDrafts({ noteId }: { noteId: number }) {
  const { t } = useTranslation();
  const drafts = useNotesStore((s) => s.drafts[noteId] ?? NO_DRAFTS);
  const blocks = useNotesStore((s) => s.notes[noteId]?.blocks);
  const [copied, setCopied] = useState<string | null>(null);
  const label = t("notes:draft.notSaved", "Not saved");

  const items = useMemo(
    () =>
      (blocks ?? [])
        .filter((b) => b.id in drafts)
        .map((b) => ({ id: b.id, text: blockText(b.type, b.props, b.content) })),
    [blocks, drafts],
  );
  // The last drafts stay on screen while the notice closes, so it closes
  // with its content instead of emptying first.
  const [held, setHeld] = useState(items);
  if (items.length && items !== held) setHeld(items);
  const shown = items.length || prefersReducedMotion() ? items : held;

  const copy = async (id: string, text: string) => {
    try {
      if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
        throw new Error("no clipboard");
      }
      await navigator.clipboard.writeText(text);
      setCopied(id);
    } catch {
      toast.error(t("notes:draft.copyFailed", "Couldn't copy. Select the text and copy it yourself."));
    }
  };

  const style = items
    .map(
      (d) =>
        `.paperhub-notes .bn-block-outer[data-id="${cssId(d.id)}"] > .bn-block > .bn-block-content {` +
        " outline: 1px dashed rgb(245 158 11 / 0.7); outline-offset: 2px; border-radius: 4px;" +
        " background: rgb(245 158 11 / 0.08); }",
    )
    .join("\n");

  return (
    <>
      {items.length > 0 && <style data-unsaved="">{style}</style>}
      <div
        data-testid="unsaved-drafts"
        data-state={items.length ? "open" : "closed"}
        onTransitionEnd={(e) => {
          if (e.target === e.currentTarget && !items.length) setHeld([]);
        }}
        className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <div className="min-h-0 overflow-hidden">
          <ul className="mx-3 mb-1 flex flex-col gap-0.5 rounded-md border border-dashed border-amber-500/60 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">
            {shown.map((d) => (
              <li key={d.id} className="flex min-w-0 items-center gap-1.5">
                <CloudOff className="size-3.5 shrink-0" aria-hidden />
                <span className="shrink-0 font-medium">{label}</span>
                <span
                  className="min-w-0 flex-1 truncate text-foreground/80"
                  {...truncatedTitle(d.text)}
                >
                  {d.text}
                </span>
                <button
                  type="button"
                  aria-label={t("notes:draft.copy", "Copy")}
                  title={t("notes:draft.hint", "Kept on this screen only. Edit it to try again.")}
                  className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:bg-amber-500/20"
                  onClick={() => void copy(d.id, d.text)}
                >
                  {copied === d.id ? (
                    <Check className="size-3" aria-hidden />
                  ) : (
                    <Copy className="size-3" aria-hidden />
                  )}
                  {copied === d.id ? t("notes:draft.copied", "Copied") : t("notes:draft.copy", "Copy")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}
