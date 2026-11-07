import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, CircleCheck, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ConversationContext } from "@/components/feedback/ConversationContext";
import { Button } from "@/components/ui/button";
import { Collapsible } from "@/components/ui/collapsible";
import { usePresence } from "@/hooks/usePresence";
import { getFeedbackQueue, submitFeedbackQueue } from "@/lib/api";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { useNavStore } from "@/store/nav";
import { REASONS, type QueueItem } from "@/types/feedback";

// Slide timing: the old item leaves (~160 ms of the 180 ms transition), then
// the new one enters from the other side.
const SLIDE_OUT_MS = 160;
const DONE_SHOW_MS = 2200;
const DONE_FADE_MS = 400;
const CARD_EXIT_MS = 200;
// The body's Collapsible height transition (duration-200) plus a margin.
const COLLAPSE_MS = 220;

type Phase = "idle" | "out-left" | "out-right" | "in-left" | "in-right";

const PHASE_CLASS: Record<Phase, string> = {
  idle: "translate-x-0 opacity-100",
  "out-left": "-translate-x-8 opacity-0",
  "out-right": "translate-x-8 opacity-0",
  // Entry start positions: placed without a transition, then released to idle.
  "in-left": "-translate-x-8 opacity-0 transition-none",
  "in-right": "translate-x-8 opacity-0 transition-none",
};

const POPUP_ROLES =
  "[role='dialog'],[role='alertdialog'],[role='menu'],[role='menuitem'],[role='listbox']";
const OPEN_POPUP = ["dialog", "alertdialog", "menu", "listbox"]
  .map((r) => `[role='${r}'][data-open]`)
  .join(",");

/** True when a key event comes from a field the user is typing in. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
}

interface ConfirmQueueProps {
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  /** Called after a successful submit (the page refetches its analysis). */
  onSubmitted?: () => void;
}

/**
 * The Feedback page's confirmation queue: a banner that expands into a
 * full-page card where the user confirms or rejects each implicit flag the
 * reviewer was unsure about. Answers are a draft until "Submit N".
 */
export function ConfirmQueue({ expanded, onExpandedChange, onSubmitted }: ConfirmQueueProps) {
  const { t } = useTranslation("feedback");
  const setBadge = useNavStore((s) => s.setFeedbackBadge);

  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [idx, setIdx] = useState(0);
  const [shown, setShown] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [answers, setAnswers] = useState<Record<number, boolean>>({});
  const [reasons, setReasons] = useState<Record<number, string[]>>({});
  const [submitting, setSubmitting] = useState(false);
  const [allDone, setAllDone] = useState(false);
  const [lastCount, setLastCount] = useState(0);
  // The final submit's collapse: items stay rendered until it has played.
  const [closing, setClosing] = useState(false);
  // Bumped each time the body finishes expanding: re-scrolls the conversation.
  const [openCount, setOpenCount] = useState(0);
  const dotsRef = useRef<HTMLSpanElement>(null);
  const timers = useRef<number[]>([]);

  const count = items?.length ?? 0;
  const card = usePresence(count > 0, CARD_EXIT_MS);
  const done = usePresence(allDone, DONE_FADE_MS);
  if (count > 0 && count !== lastCount) setLastCount(count);

  useEffect(() => {
    let alive = true;
    getFeedbackQueue().then(
      (q) => {
        if (!alive) return;
        setItems(q);
        setBadge(q.length);
      },
      () => {
        if (alive) toast.error(t("queue.loadFailed"));
      },
    );
    return () => {
      alive = false;
    };
  }, [setBadge, t]);

  // Latest state for `reload`, which runs from async callbacks.
  const live = useRef({ items, idx, expanded });
  useEffect(() => {
    live.current = { items, idx, expanded };
  });
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  const reloadSeq = useRef(0);

  /** Refetch the queue and merge it in place: the item on screen stays on
   * screen if it is still queued, and draft answers of remaining items are
   * kept. A failed reload keeps what is shown. */
  const reload = useCallback(() => {
    const mine = ++reloadSeq.current;
    getFeedbackQueue().then(
      (q) => {
        if (!mounted.current || mine !== reloadSeq.current) return;
        const { items: prev, idx: at, expanded: open } = live.current;
        const curId = prev?.[at]?.feedback_id;
        const keep = q.findIndex((it) => it.feedback_id === curId);
        const to = keep >= 0 ? keep : Math.max(0, Math.min(at, q.length - 1));
        setItems(q);
        setIdx(to);
        setShown(to);
        setBadge(q.length);
        if (!q.length && open) onExpandedChange(false);
      },
      () => undefined,
    );
  }, [setBadge, onExpandedChange]);

  // The sidebar badge polls the queue: when it disagrees with what is shown
  // (a new review queued an item, or one left), bring the page up to date.
  const badge = useNavStore((s) => s.feedbackBadge);
  useEffect(() => {
    if (items === null || submitting || closing) return;
    if (badge !== items.length) reload();
  }, [badge, items, submitting, closing, reload]);

  const clearTimers = () => {
    for (const id of timers.current) clearTimeout(id);
    timers.current = [];
  };
  useEffect(() => clearTimers, []);

  const reasonsOf = useCallback(
    (it: QueueItem) => reasons[it.feedback_id] ?? it.guessed_reasons,
    [reasons],
  );

  /** Slide the body out toward `dir`, run `swap`, slide the new content in. */
  const slide = useCallback((dir: 1 | -1, swap: () => void) => {
    clearTimers();
    if (prefersReducedMotion()) {
      swap();
      setPhase("idle");
      return;
    }
    setPhase(dir > 0 ? "out-left" : "out-right");
    timers.current.push(
      window.setTimeout(() => {
        swap();
        setPhase(dir > 0 ? "in-right" : "in-left");
        // Two frames: paint the start position, then release the transition.
        requestAnimationFrame(() => requestAnimationFrame(() => setPhase("idle")));
      }, SLIDE_OUT_MS),
    );
  }, []);

  /** Navigate to item `to`, sliding in the direction of travel. */
  const go = useCallback(
    (to: number, dir: 1 | -1) => {
      // Never while a submit is settling: its pending swap must not be cancelled.
      if (!items?.length || closing || submitting) return;
      const target = (to + items.length) % items.length;
      if (target === idx) return;
      setIdx(target);
      slide(dir, () => setShown(target));
    },
    [items, idx, closing, submitting, slide],
  );

  const answer = (confirmed: boolean) => {
    if (!items || closing || submitting) return;
    // The item on screen: mid-slide it can still be the one being left.
    const it = items[shown];
    if (!it) return;
    const next = { ...answers, [it.feedback_id]: confirmed };
    setAnswers(next);
    const open = (i: number) => next[items[i]!.feedback_id] === undefined;
    let to = items.findIndex((_, i) => i > shown && open(i));
    if (to < 0) to = items.findIndex((_, i) => open(i));
    if (to >= 0 && to !== shown) go(to, 1);
  };

  const toggleReason = (it: QueueItem, r: string) => {
    const cur = reasonsOf(it);
    setReasons({
      ...reasons,
      [it.feedback_id]: cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r],
    });
  };

  const answeredItems = (items ?? []).filter((it) => answers[it.feedback_id] !== undefined);
  const nAnswered = answeredItems.length;

  const submit = useCallback(async () => {
    if (!items || submitting) return;
    const picked = items.filter((it) => answers[it.feedback_id] !== undefined);
    if (!picked.length) return;
    setSubmitting(true);
    let res;
    try {
      res = await submitFeedbackQueue({
        answers: picked.map((it) => {
          const confirmed = answers[it.feedback_id]!;
          const chosen = reasonsOf(it);
          // Keep the canonical order for the known reasons, presets after.
          const ordered = [
            ...REASONS.filter((r) => chosen.includes(r)),
            ...chosen.filter((r) => !(REASONS as readonly string[]).includes(r)),
          ];
          return { feedback_id: it.feedback_id, confirmed, reasons: confirmed ? ordered : [] };
        }),
      });
    } catch {
      toast.error(t("queue.submitFailed"));
      setSubmitting(false);
      // Some items may have left the queue meanwhile (the cause of a 404).
      reload();
      return;
    }
    toast.success(t("queue.submitted", { count: picked.length, confirmed: res.confirmed }));
    onSubmitted?.();
    const ids = new Set(picked.map((it) => it.feedback_id));
    const rest = items.filter((it) => !ids.has(it.feedback_id));
    setBadge(rest.length);
    if (rest.length) {
      // The remaining items slide in forward; `submitting` holds until the
      // swap so a second Ctrl+Enter can't resend the same answers.
      slide(1, () => {
        setItems(rest);
        setAnswers({});
        setIdx(0);
        setShown(0);
        setSubmitting(false);
      });
      return;
    }
    // Queue empty: collapse the card with its content still rendered, then
    // drop the items once the collapse has played (the card then fades out).
    clearTimers();
    setClosing(true);
    onExpandedChange(false);
    setAllDone(true);
    timers.current.push(
      window.setTimeout(
        () => {
          setItems([]);
          setAnswers({});
          setIdx(0);
          setShown(0);
          setPhase("idle");
          setClosing(false);
          setSubmitting(false);
        },
        prefersReducedMotion() ? 0 : COLLAPSE_MS,
      ),
      window.setTimeout(() => setAllDone(false), DONE_SHOW_MS),
    );
  }, [items, submitting, answers, reasonsOf, t, setBadge, onExpandedChange, onSubmitted, slide, reload]);

  const toggleExpanded = () => {
    if (closing || submitting) return;
    if (!expanded && items) {
      const first = items.findIndex((it) => answers[it.feedback_id] === undefined);
      const start = Math.max(0, first);
      clearTimers();
      setIdx(start);
      setShown(start);
      setPhase("idle");
    }
    onExpandedChange(!expanded);
  };

  // Keyboard: page-scoped (this component only mounts on the Feedback page),
  // active while the card is open, never while typing or inside a dialog.
  const answerRef = useRef(answer);
  useEffect(() => {
    answerRef.current = answer;
  });
  useEffect(() => {
    if (!expanded || !count) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || isTyping(e.target)) return;
      if (e.target instanceof Element && e.target.closest(POPUP_ROLES)) return;
      // Any open popup (dialog, menu, listbox, popover) owns the keyboard.
      if (document.querySelector(OPEN_POPUP)) return;
      const plain = !e.ctrlKey && !e.metaKey && !e.altKey;
      if (e.key === "ArrowLeft" && plain) {
        e.preventDefault();
        go(idx - 1, -1);
      } else if (e.key === "ArrowRight" && plain) {
        e.preventDefault();
        go(idx + 1, 1);
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        void submit();
      } else if (plain && !e.repeat && (e.key === "y" || e.key === "Y")) {
        answerRef.current(true);
      } else if (plain && !e.repeat && (e.key === "n" || e.key === "N")) {
        answerRef.current(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [expanded, count, go, idx, submit]);

  useEffect(() => {
    const dot = dotsRef.current?.children[idx];
    if (dot instanceof HTMLElement && typeof dot.scrollIntoView === "function") {
      dot.scrollIntoView({
        block: "nearest",
        inline: "nearest",
        behavior: prefersReducedMotion() ? "auto" : "smooth",
      });
    }
  }, [idx]);

  const cur = items?.[idx];
  const body = items?.[shown];
  const bannerCount = count || lastCount;

  return (
    <div
      className={cn(
        "flex min-h-0 flex-col px-5 transition-[flex-grow,padding] duration-[280ms] ease-out motion-reduce:transition-none",
        card.mounted || done.mounted ? "pt-3" : "pt-0",
        expanded ? "grow pb-5" : "grow-0 pb-0",
      )}
    >
      {card.mounted && (
        <section
          data-state={card.state}
          className={cn(
            "flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-card duration-200",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-top-1",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-1",
            "motion-reduce:animate-none",
            expanded ? "grow" : "grow-0",
          )}
        >
          <div className="flex shrink-0 items-center gap-2.5 px-3.5 py-2.5 text-[13px]">
            <span className="inline-flex h-[22px] min-w-[22px] items-center justify-center rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground">
              {bannerCount}
            </span>
            <span className="min-w-0">
              <b className="font-semibold">{t("queue.count", { count: bannerCount })}</b>
              <span className="ml-2 text-muted-foreground">{t("queue.hint")}</span>
            </span>
            <span className="flex-1" />
            <Button
              type="button"
              size="sm"
              variant={expanded ? "outline" : "default"}
              aria-expanded={expanded}
              disabled={closing || submitting}
              onClick={toggleExpanded}
            >
              {expanded ? t("queue.collapse") : t("queue.start")}
            </Button>
          </div>

          <Collapsible
            open={expanded && count > 0}
            className="min-h-0 flex-auto"
            onExpanded={() => setOpenCount((n) => n + 1)}
          >
            {cur && body && (
              <div className="flex h-full flex-col border-t border-border px-4 pt-0.5 pb-4">
                <div className="flex w-full max-w-[820px] min-h-0 flex-1 flex-col">
                  {/* Fixed header: question, reviewer doubt, navigation, progress. */}
                  <div className="shrink-0">
                    <div className="mt-3 mb-2 flex items-center gap-2.5 text-[13px]">
                      <b className="shrink-0 font-semibold">{t("queue.question")}</b>
                      <span
                        className="min-w-0 truncate text-muted-foreground"
                        title={cur.reviewer_doubt}
                      >
                        {t("queue.doubt", { doubt: cur.reviewer_doubt })}
                      </span>
                      <span className="flex-1" />
                      <span className="inline-flex min-w-0 max-w-[55%] shrink items-center gap-1">
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 shrink-0"
                          aria-label={t("queue.prev")}
                          title={t("queue.prev")}
                          onClick={() => go(idx - 1, -1)}
                        >
                          <ChevronLeft className="h-4 w-4" />
                        </Button>
                        <span
                          ref={dotsRef}
                          className="mx-1.5 inline-flex min-w-0 gap-1.5 overflow-x-auto p-0.5 [scrollbar-width:thin]"
                        >
                          {items.map((q, i) => {
                            const a = answers[q.feedback_id];
                            const n = i + 1;
                            const label =
                              a === true
                                ? t("queue.dotYes", { n })
                                : a === false
                                  ? t("queue.dotNo", { n })
                                  : t("queue.dotOpen", { n });
                            return (
                              <button
                                key={q.feedback_id}
                                type="button"
                                aria-label={label}
                                title={label}
                                aria-current={i === idx ? "step" : undefined}
                                onClick={() => go(i, i > idx ? 1 : -1)}
                                className={cn(
                                  "inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full border border-border text-[11px] text-muted-foreground outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                                  i === idx && "border-foreground font-semibold text-foreground",
                                  a !== undefined && "bg-accent text-foreground",
                                )}
                              >
                                {a === true ? (
                                  <Check className="h-3 w-3" aria-hidden />
                                ) : a === false ? (
                                  <X className="h-3 w-3" aria-hidden />
                                ) : (
                                  n
                                )}
                              </button>
                            );
                          })}
                        </span>
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 shrink-0"
                          aria-label={t("queue.next")}
                          title={t("queue.next")}
                          onClick={() => go(idx + 1, 1)}
                        >
                          <ChevronRight className="h-4 w-4" />
                        </Button>
                      </span>
                    </div>
                    <div
                      role="progressbar"
                      aria-label={t("queue.progress", { answered: nAnswered, total: count })}
                      aria-valuemin={0}
                      aria-valuemax={count}
                      aria-valuenow={nAnswered}
                      className="mb-3 h-[3px] overflow-hidden rounded-sm bg-muted"
                    >
                      <i
                        className="block h-full bg-primary transition-[width] duration-250 ease-out motion-reduce:transition-none"
                        style={{ width: `${(nAnswered / count) * 100}%` }}
                      />
                    </div>
                  </div>

                  {/* Sliding body: only the conversation scrolls. */}
                  <div className="flex min-h-0 flex-1 overflow-hidden">
                    <div
                      data-testid="queue-slide"
                      data-slide={phase}
                      className={cn(
                        "flex min-w-0 flex-1 flex-col transition-[transform,opacity] duration-[180ms] ease-out motion-reduce:transition-none",
                        PHASE_CLASS[phase],
                      )}
                    >
                      <ConversationContext key={body.feedback_id} item={body} scrollKey={openCount} />
                      <div className="mt-3 shrink-0 text-[13px]">
                        {t("queue.whatWrong")}
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {[
                            ...REASONS,
                            ...body.guessed_reasons.filter(
                              (r) => !(REASONS as readonly string[]).includes(r),
                            ),
                          ].map((r) => {
                            const on = reasonsOf(body).includes(r);
                            return (
                              <button
                                key={r}
                                type="button"
                                aria-pressed={on}
                                onClick={() => toggleReason(body, r)}
                                className={cn(
                                  "inline-flex h-7 items-center gap-1 rounded-full border border-border px-2.5 text-xs transition-colors outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
                                  on && "border-foreground bg-accent hover:bg-accent",
                                )}
                              >
                                {on && <Check className="h-3 w-3" aria-hidden />}
                                {(REASONS as readonly string[]).includes(r)
                                  ? t(`reason.${r}`)
                                  : r}
                                {body.guessed_reasons.includes(r) && (
                                  <span className="text-[10.5px] text-muted-foreground">
                                    {t("queue.guess")}
                                  </span>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                      <div className="mt-4 flex shrink-0 flex-wrap gap-2">
                        {([true, false] as const).map((yes) => {
                          const sel = answers[body.feedback_id] === yes;
                          return (
                            <Button
                              key={String(yes)}
                              type="button"
                              variant={sel ? "default" : "outline"}
                              aria-pressed={sel}
                              className={cn("h-9", sel && "outline-2 outline-offset-2 outline-ring")}
                              onClick={() => answer(yes)}
                            >
                              {yes ? t("queue.yes") : t("queue.no")}
                              <kbd className="ml-1.5 rounded border border-b-2 border-border px-1.5 text-[11px] text-muted-foreground">
                                {yes ? "Y" : "N"}
                              </kbd>
                            </Button>
                          );
                        })}
                      </div>
                    </div>
                  </div>

                  {/* Fixed submit row. */}
                  <div className="mt-3.5 flex shrink-0 items-center gap-2.5 border-t border-border pt-3 text-[12.5px]">
                    <span className="text-muted-foreground">
                      {t("queue.answered", { answered: nAnswered, total: count })}
                      {nAnswered < count && t("queue.leftover")}
                      {" · "}
                      {t("queue.keys")}
                    </span>
                    <span className="flex-1" />
                    <Button
                      type="button"
                      size="sm"
                      disabled={!nAnswered || submitting}
                      onClick={() => void submit()}
                    >
                      {t("queue.submit", { count: nAnswered })}
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </Collapsible>
        </section>
      )}

      {done.mounted && (
        <div
          role="status"
          data-state={done.state}
          className={cn(
            "flex items-center gap-2 rounded-lg border border-border px-3.5 py-2.5 text-[13px] text-muted-foreground",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:duration-200",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:duration-400",
            "motion-reduce:animate-none",
            card.mounted && "mt-2",
          )}
        >
          <CircleCheck className="h-4 w-4 shrink-0" aria-hidden />
          {t("queue.allDone")}
        </div>
      )}
    </div>
  );
}
