import { useEffect } from "react";

import { getHeldSearches, getPausedCards } from "@/lib/api";
import { useNavStore } from "@/store/nav";

const POLL_MS = 30_000;

/**
 * Keeps the sidebar's waiting counts current (the "Held searches" badge and
 * each chat row's marker, which also counts the Ask-first actions paused
 * runs wait on): on mount, every 30 s, whenever the window regains focus and
 * whenever `refreshHeld()` is called (a card decided, a run paused or
 * resumed). A failed request keeps the last counts.
 */
export function useHeldBadge(): void {
  const seqTrigger = useNavStore((s) => s.heldRefreshSeq);
  useEffect(() => {
    let alive = true;
    let seq = 0;
    const refresh = (): void => {
      const mine = ++seq;
      getHeldSearches().then(
        (rows) => {
          if (alive && mine === seq) useNavStore.getState().setHeldList(rows);
        },
        () => undefined,
      );
      getPausedCards().then(
        (rows) => {
          if (alive && mine === seq) useNavStore.getState().setPausedList(rows);
        },
        () => undefined,
      );
    };
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    window.addEventListener("focus", refresh);
    return () => {
      alive = false;
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [seqTrigger]);
}
