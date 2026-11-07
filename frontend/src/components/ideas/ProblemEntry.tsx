import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { ChevronDown, Loader2, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Collapsible } from "@/components/ui/collapsible";
import { Textarea } from "@/components/ui/textarea";
import { usePresence } from "@/hooks/usePresence";
import { revealInScroll } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { useIdeationStore } from "@/store/ideation";

/** Mirrors the backend's PROBLEM_MAX_CHARS / TRIED_MAX_CHARS. */
export const PROBLEM_MAX_CHARS = 2000;
/** The counter shows from this share of the limit. */
const COUNTER_FROM = 0.8;
/** The entry grows with its text up to this height (px), then scrolls. */
const MAX_ENTRY_HEIGHT = 160;

/** The problem entry box: what's stuck, plus a collapsible "what I already tried". */
export function ProblemEntry({ inputRef }: { inputRef: RefObject<HTMLTextAreaElement | null> }) {
  const { t } = useTranslation("ideas");
  const [text, setText] = useState("");
  const [tried, setTried] = useState("");
  const [triedOpen, setTriedOpen] = useState(false);
  const triedId = useId();
  const submitting = useIdeationStore((s) => s.submitting);
  const submitProblem = useIdeationStore((s) => s.submitProblem);

  // Grow with the text (up to a cap) so a long problem stays in view.
  const own = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = own.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_ENTRY_HEIGHT)}px`;
  }, [text]);
  const nearLimit = text.length >= PROBLEM_MAX_CHARS * COUNTER_FROM;
  const counter = usePresence(nearLimit);
  // The counter keeps its last count while it fades out.
  const [lastCount, setLastCount] = useState(text.length);
  if (nearLimit && lastCount !== text.length) setLastCount(text.length);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Plain Enter submits, Shift+Enter is a newline; an IME's confirming Enter is neither.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  };

  const submit = async () => {
    const q = text.trim();
    if (!q || submitting) return;
    try {
      await submitProblem(q, tried.trim());
      setText("");
      setTried("");
      setTriedOpen(false);
    } catch {
      toast.error(t("entry.failed", "Couldn't create the problem"));
    }
  };

  return (
    <form
      className="flex flex-1 min-w-0 flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="flex items-start gap-2">
        <div className="relative min-w-0 flex-1">
          <Textarea
            ref={(el) => {
              own.current = el;
              inputRef.current = el;
            }}
            value={text}
            rows={1}
            maxLength={PROBLEM_MAX_CHARS}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t(
              "entry.placeholder",
              "Describe a new problem: where you're stuck, the constraints",
            )}
            aria-label={t("entry.label", "Problem")}
            className={cn(
              "min-h-9 resize-none py-[7px] leading-snug field-sizing-fixed transition-[height,color,box-shadow,border-color] duration-150 ease-out motion-reduce:transition-none",
              nearLimit && "pr-14",
            )}
          />
          {counter.mounted && (
            <span
              data-testid="entry-counter"
              data-state={counter.state}
              aria-live="polite"
              className={cn(
                "pointer-events-none absolute right-5 bottom-1.5 text-[11px] tabular-nums duration-150 fill-mode-forwards",
                "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 motion-reduce:animate-none",
                lastCount >= PROBLEM_MAX_CHARS ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {t("entry.counter", "{{count}}/{{max}}", { count: lastCount, max: PROBLEM_MAX_CHARS })}
            </span>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="lg"
          aria-expanded={triedOpen}
          aria-controls={triedId}
          onClick={() => setTriedOpen((o) => !o)}
          className="shrink-0 text-muted-foreground"
        >
          {t("entry.tried", "What I already tried")}
          <ChevronDown
            aria-hidden="true"
            className={cn(
              "transition-transform duration-200 ease-out motion-reduce:transition-none",
              triedOpen && "rotate-180",
            )}
          />
        </Button>
        <Button type="submit" size="lg" disabled={!text.trim() || submitting}>
          {submitting ? (
            <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          ) : (
            <Sparkles aria-hidden="true" />
          )}
          {t("entry.submit", "Find cross-domain ideas")}
        </Button>
      </div>
      <Collapsible open={triedOpen} onExpanded={(el) => revealInScroll(el)}>
        <div id={triedId} className="pt-2">
          <Textarea
            value={tried}
            onChange={(e) => setTried(e.target.value)}
            rows={2}
            maxLength={PROBLEM_MAX_CHARS}
            placeholder={t(
              "entry.triedPlaceholder",
              "Approaches you've tried and why they fell short (optional)",
            )}
            aria-label={t("entry.tried", "What I already tried")}
          />
        </div>
      </Collapsible>
    </form>
  );
}
