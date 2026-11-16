/** A comment thread on note text: the card (quote, replies, a box that posts
 * on Enter) and the popover that holds it under the commented words. A comment
 * is the user's remark beside the note, not note content (the agent only reads
 * open ones). Resolve / Delete with Undo live in commentActions.ts.
 */
import { Popover as PopoverPrimitive } from "@base-ui/react/popover";
import { ArrowUp, Check, Trash2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import type { NoteComment } from "@/types/notes";

// ---- the card -----------------------------------------------------------------

export interface CommentThreadProps {
  /** The thread; null while a new comment is being written. */
  thread: NoteComment | null;
  quote: string;
  /** Posts `text` (starts the thread when it is new). */
  onPost: (text: string) => void | Promise<void>;
  onResolve: () => void;
  onDelete: () => void;
  onClose: () => void;
  /** Esc on a new comment with nothing written. */
  onDiscard: () => void;
}

function when(iso: string, lang: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });
}

const iconBtn =
  "inline-flex size-[26px] items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40";

export function CommentThread(props: CommentThreadProps) {
  const { thread, quote, onPost, onResolve, onDelete, onClose, onDiscard } = props;
  const { t, i18n } = useTranslation();
  const [text, setText] = useState("");
  const [posting, setPosting] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const items = thread?.items ?? [];
  const n = items.length;

  useEffect(() => {
    box.current?.focus();
  }, [n]);
  // Grows with what is typed, up to a cap.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [text]);

  const send = async () => {
    const body = text.trim();
    if (!body || posting) return;
    setPosting(true);
    try {
      await onPost(body);
      setText("");
    } catch {
      toast.error(t("notes:comment.failed", "Couldn't save the comment."));
    } finally {
      setPosting(false);
    }
  };

  const resolveLabel = t("notes:comment.resolve", "Resolve");
  const deleteLabel = t("notes:comment.deleteThread", "Delete the thread");
  const closeLabel = t("notes:comment.close", "Close (Esc)");
  const sendLabel = t("notes:comment.send", "Comment (Enter)");

  return (
    <div data-testid="comment-thread" className="text-[13px]">
      <div className="mb-1 flex items-center gap-0.5">
        <span className="mr-auto text-[11.5px] text-muted-foreground">
          {n === 0
            ? t("notes:comment.new", "New comment")
            : n === 1
              ? t("notes:comment.countOne", "1 comment")
              : t("notes:comment.countMany", "{{count}} comments", { count: n })}
        </span>
        {n > 0 && (
          <>
            <button type="button" className={iconBtn} onClick={onResolve} title={resolveLabel} aria-label={resolveLabel}>
              <Check className="size-3.5" aria-hidden />
            </button>
            <button type="button" className={iconBtn} onClick={onDelete} title={deleteLabel} aria-label={deleteLabel}>
              <Trash2 className="size-3.5" aria-hidden />
            </button>
          </>
        )}
        <button type="button" className={iconBtn} onClick={onClose} title={closeLabel} aria-label={closeLabel}>
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
      <div className="mb-1 truncate border-l-2 border-border pl-1.5 text-[11.5px] text-muted-foreground" title={quote}>
        “{quote}”
      </div>
      {items.map((it) => (
        <div key={it.id} className="px-0.5 py-1">
          <div className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
            <b className="font-semibold text-foreground">{t("notes:comment.you", "You")}</b>
            {when(it.created_at, i18n.language)}
          </div>
          <p className="mt-0.5 whitespace-pre-wrap">{it.text}</p>
        </div>
      ))}
      <div className="mt-1.5 flex items-end gap-1.5 rounded-lg border border-border bg-background py-1 pl-2 pr-1">
        <textarea
          ref={box}
          rows={1}
          value={text}
          aria-label={t("notes:comment.label", "Comment")}
          placeholder={n ? t("notes:comment.reply", "Reply…") : t("notes:comment.add", "Add a comment…")}
          className="max-h-[120px] min-h-5 flex-1 resize-none border-0 bg-transparent py-0.5 text-[13px] text-foreground outline-none"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            } else if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              if (!thread && !text.trim()) onDiscard();
              else onClose();
            }
          }}
        />
        <button
          type="button"
          className={iconBtn}
          disabled={!text.trim() || posting}
          onClick={() => void send()}
          title={sendLabel}
          aria-label={sendLabel}
        >
          <ArrowUp className="size-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}

// ---- the popover --------------------------------------------------------------

/** Holds a thread under its words. It grows from the words and shrinks back
 * (the ui/ popover motion); reduced motion turns the animation off. */
export function CommentPopover(props: {
  open: boolean;
  anchor: { getBoundingClientRect: () => DOMRect } | null;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <PopoverPrimitive.Root open={props.open} onOpenChange={props.onOpenChange}>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          anchor={props.anchor}
          side="bottom"
          align="start"
          sideOffset={6}
          className="isolate z-[81]"
        >
          <PopoverPrimitive.Popup
            aria-label={t("notes:comment.label", "Comment")}
            initialFocus={false}
            className="w-[300px] rounded-[10px] border border-border bg-popover p-2 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 motion-reduce:animate-none"
          >
            {props.children}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
