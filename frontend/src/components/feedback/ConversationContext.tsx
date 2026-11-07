import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, MessageSquare } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Collapsible } from "@/components/ui/collapsible";
import { cleanForPreview } from "@/lib/answerPreview";
import { CHUNK_MARKER_RE } from "@/lib/chunkCitations";
import { prefersReducedMotion, revealInScroll } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { QueueItem, QueueTurn } from "@/types/feedback";

// Matches the clamp's height transition (duration-200) plus a margin.
const CLAMP_MS = 220;

// Chunk markers (single and multi-id, shared with the chat) or a bare `[3]`.
const CITE_RE = new RegExp(`${CHUNK_MARKER_RE.source}|\\[\\d+\\]`, "g");

/** Citation markers (`[3]`, `[chunk:12]`, `[chunk:12, 13]`) rendered as quiet
 * superscripts. */
function withCites(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE_RE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    out.push(
      <sup key={at} className="text-[10.5px] text-muted-foreground">
        {m[0]}
      </sup>,
    );
    last = at + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Animate `el` from its current height to the height after `change` runs. */
function animateHeight(el: HTMLElement, change: () => void): void {
  if (prefersReducedMotion()) {
    change();
    return;
  }
  const from = el.getBoundingClientRect().height;
  el.style.height = `${from}px`;
  change();
  requestAnimationFrame(() => {
    el.style.height = "auto";
    const to = el.getBoundingClientRect().height;
    el.style.height = `${from}px`;
    void el.offsetHeight;
    el.style.height = `${to}px`;
    setTimeout(() => {
      el.style.height = "";
    }, CLAMP_MS);
  });
}

/**
 * Assistant text clamped to `lines`; "Show full text" appears only when the
 * clamp actually truncates (`scrollHeight > clientHeight`). Expanding grows
 * the block in place and reveals it inside the conversation scroller.
 */
function Clamped({
  text,
  lines,
  className,
}: {
  text: string;
  lines: 2 | 3 | 6;
  className?: string;
}) {
  const { t } = useTranslation("feedback");
  const box = useRef<HTMLDivElement>(null);
  const tx = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [truncated, setTruncated] = useState(false);
  // The reveal after expanding; cancelled if the item leaves first.
  const revealTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (revealTimer.current !== null) clearTimeout(revealTimer.current);
    },
    [],
  );

  useEffect(() => {
    const el = tx.current;
    if (!el || open) return;
    const measure = () => setTruncated(el.scrollHeight > el.clientHeight + 1);
    const raf = requestAnimationFrame(measure);
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [text, open]);

  const toggle = () => {
    const el = box.current;
    const next = !open;
    if (el) animateHeight(el, () => setOpen(next));
    else setOpen(next);
    if (next && el) {
      if (revealTimer.current !== null) clearTimeout(revealTimer.current);
      revealTimer.current = window.setTimeout(() => {
        revealTimer.current = null;
        revealInScroll(el);
      }, CLAMP_MS);
    }
  };

  return (
    <div
      ref={box}
      data-clamp
      className={cn(
        "overflow-hidden transition-[height] duration-200 ease-out motion-reduce:transition-none",
        className,
      )}
    >
      <div
        ref={tx}
        className={cn(
          "whitespace-pre-wrap",
          !open && (lines === 2 ? "line-clamp-2" : lines === 3 ? "line-clamp-3" : "line-clamp-6"),
        )}
      >
        {withCites(cleanForPreview(text))}
      </div>
      {(truncated || open) && (
        <button
          type="button"
          onClick={toggle}
          className="mt-0.5 text-xs text-muted-foreground underline underline-offset-2 outline-none hover:text-foreground focus-visible:text-foreground"
        >
          {open ? t("queue.showLess") : t("queue.showFull")}
        </button>
      )}
    </div>
  );
}

const userCls =
  "ml-auto mb-2.5 w-fit max-w-[80%] whitespace-pre-wrap rounded-2xl bg-muted px-3 py-1.5 text-[13.5px]";
const assistantCls = "mb-2.5 text-[13.5px] leading-relaxed text-foreground";
const labelCls = "mb-1 text-[11.5px] text-muted-foreground";

function Turn({ turn, old }: { turn: QueueTurn; old?: boolean }) {
  return turn.role === "user" ? (
    <div className={cn(userCls, old && "opacity-70")}>{turn.content}</div>
  ) : (
    <Clamped text={turn.content} lines={2} className={cn(assistantCls, old && "opacity-70")} />
  );
}

/**
 * The conversation around a queued answer: session, papers and run, the
 * earlier turns (collapsible in place), the questioned exchange, the flagged
 * follow-up and the system's next reply. This is the card's only scroller;
 * it opens scrolled to the questioned exchange, and scrolls back there
 * whenever `scrollKey` changes (each time the card finishes expanding).
 */
export function ConversationContext({
  item,
  scrollKey = 0,
}: {
  item: QueueItem;
  scrollKey?: number;
}) {
  const { t } = useTranslation("feedback");
  const scroller = useRef<HTMLDivElement>(null);
  const question = useRef<HTMLDivElement>(null);
  const [earlierOpen, setEarlierOpen] = useState(true);
  const nEarlier = item.earlier.length;

  useEffect(() => {
    const c = scroller.current;
    const q = question.current;
    if (!c || !q) return;
    const raf = requestAnimationFrame(() => {
      const offset = q.getBoundingClientRect().top - c.getBoundingClientRect().top;
      c.scrollTop = Math.max(0, c.scrollTop + offset - 12);
    });
    return () => cancelAnimationFrame(raf);
  }, [item.feedback_id, scrollKey]);

  return (
    <div
      ref={scroller}
      className="min-h-0 flex-[0_1_auto] overflow-y-auto rounded-lg border border-border bg-card px-4 py-3.5"
    >
      <div className="mb-3 flex items-center gap-2 border-b border-border pb-2.5 text-xs text-muted-foreground">
        <MessageSquare className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <b className="truncate font-semibold text-foreground">{item.session_title}</b>
        {item.papers.length > 0 && (
          <span className="truncate">· {item.papers.join(" · ")}</span>
        )}
        <span className="flex-1" />
        <span className="shrink-0">{t("queue.run", { id: item.run_id })}</span>
      </div>

      {nEarlier > 0 && (
        <>
          <button
            type="button"
            aria-expanded={earlierOpen}
            onClick={() => setEarlierOpen((v) => !v)}
            className="mb-3 flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-border p-1.5 text-xs text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronDown
              aria-hidden
              className={cn(
                "h-3.5 w-3.5 transition-transform duration-200 motion-reduce:transition-none",
                earlierOpen && "rotate-180",
              )}
            />
            {earlierOpen
              ? t("queue.earlierHide", { count: nEarlier })
              : t("queue.earlierShow", { count: nEarlier })}
          </button>
          <Collapsible open={earlierOpen}>
            {item.earlier.map((turn, i) => (
              <Turn key={i} turn={turn} old />
            ))}
          </Collapsible>
        </>
      )}

      <div ref={question} data-question className={userCls}>
        {item.question}
      </div>
      <div className={labelCls}>{t("queue.questioned")}</div>
      <Clamped text={item.answer} lines={6} className={assistantCls} />

      {item.flagged !== "" && (
        <>
          <div className={cn(labelCls, "text-right")}>{t("queue.userThen")}</div>
          <div
            data-flagged="true"
            className={cn(userCls, "outline-2 outline-offset-2 outline-ring")}
          >
            {item.flagged}
          </div>
        </>
      )}

      {item.next_reply !== null && (
        <>
          <div className={cn(labelCls, "mt-3")}>{t("queue.nextReply")}</div>
          <Clamped
            text={item.next_reply}
            lines={2}
            className="border-l-2 border-border pl-2.5 text-[13.5px] leading-relaxed text-muted-foreground"
          />
        </>
      )}
    </div>
  );
}
