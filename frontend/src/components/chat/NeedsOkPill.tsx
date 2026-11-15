import { ArrowDown, ArrowUp, ShieldQuestion } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";

import { usePresence } from "@/hooks/usePresence";
import { useNavStore } from "@/store/nav";

type Target = { id: string; dir: "up" | "down" };

/** A waiting card's id: a held search (`data-held-id`) or an action. */
function cardId(el: HTMLElement): string | undefined {
  return el.dataset.heldId ?? el.dataset.actionId;
}

/**
 * Pill centred above the composer when the chat has a card waiting for the
 * user's OK (a held search or an Ask-first action; H5a) that is scrolled out
 * of the thread's view. The arrow points toward the first such card; a click
 * jumps to it (scroll, ring and focus, via `focusHeldId`). It fades and
 * slides in and out.
 */
export function NeedsOkPill({
  scrollerRef,
  cardIds,
}: {
  /** The thread's scroll container (the observer root). */
  scrollerRef: RefObject<HTMLElement | null>;
  /** Waiting card ids in thread order; each card carries `data-held-id`
   *  or `data-action-id`. */
  cardIds: string[];
}) {
  const { t } = useTranslation("chat");
  const [target, setTarget] = useState<Target | null>(null);
  const key = cardIds.join("|");
  const seen = useRef(new Map<string, { visible: boolean; above: boolean }>());

  useEffect(() => {
    const root = scrollerRef.current;
    const ids = key ? key.split("|") : [];
    if (!root || ids.length === 0 || typeof IntersectionObserver === "undefined") return;
    const state = seen.current;
    state.clear();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = cardId(e.target as HTMLElement);
          if (!id) continue;
          const rootTop = e.rootBounds?.top ?? 0;
          state.set(id, { visible: e.isIntersecting, above: e.boundingClientRect.top < rootTop });
        }
        const hidden = ids.find((id) => state.get(id)?.visible === false);
        setTarget(hidden ? { id: hidden, dir: state.get(hidden)!.above ? "up" : "down" } : null);
      },
      { root },
    );
    root.querySelectorAll<HTMLElement>("[data-held-id], [data-action-id]").forEach((el) => {
      if (ids.includes(cardId(el) ?? "")) io.observe(el);
    });
    return () => io.disconnect();
  }, [key, scrollerRef]);

  // A target whose card was decided meanwhile no longer counts.
  const live = target && cardIds.includes(target.id) ? target : null;
  const count = cardIds.length;
  const [shownCount, setShownCount] = useState(count);
  if (count > 0 && count !== shownCount) setShownCount(count);
  const presence = usePresence(live !== null, 200);
  const [shown, setShown] = useState<Target | null>(live);
  if (live && (live.id !== shown?.id || live.dir !== shown.dir)) setShown(live);
  if (!presence.mounted || !shown) return null;
  const Arrow = shown.dir === "up" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      data-state={presence.state}
      inert={presence.state === "closed"}
      onClick={() => useNavStore.getState().setFocusHeldId(shown.id)}
      className="absolute bottom-2.5 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-800 shadow-md duration-200 hover:bg-amber-100 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-bottom-2 data-[state=closed]:fill-mode-forwards motion-reduce:animate-none dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200 dark:hover:bg-amber-900"
    >
      <ShieldQuestion className="h-3.5 w-3.5" aria-hidden />
      {t("paused.pill", "Paused · waiting for your OK", { count: shownCount })}
      <Arrow className="h-3.5 w-3.5" aria-hidden />
    </button>
  );
}
