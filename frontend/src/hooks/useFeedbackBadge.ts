import { useEffect } from "react";

import { getFeedbackQueue } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import type { ChatSession } from "@/types/domain";

const POLL_MS = 60_000;
// The background review creates a queue item after the turn ends: look again.
const FOLLOW_UP_MS = 12_000;

/** Run ids of answers still being produced (always a session's trailing message). */
function inFlightRuns(sessions: ChatSession[]): Set<number> {
  const ids = new Set<number>();
  for (const s of sessions) {
    const last = s.messages[s.messages.length - 1];
    if (
      last?.role === "assistant" &&
      (last.status === "streaming" || last.status === "processing") &&
      last.run_id != null
    ) {
      ids.add(last.run_id);
    }
  }
  return ids;
}

/**
 * Keeps the sidebar's Feedback badge equal to the confirmation queue length:
 * on mount, after each finished chat turn (and once more ~12 s later, when
 * the background review has had time to queue its item), and every 60 s while the tab is
 * visible (paused while hidden, refreshed on return). A failed request keeps
 * the last count.
 */
export function useFeedbackBadge(): void {
  useEffect(() => {
    let alive = true;
    let seq = 0;
    let timer: ReturnType<typeof setInterval> | null = null;
    let followUp: ReturnType<typeof setTimeout> | null = null;

    const refresh = (): void => {
      const mine = ++seq;
      getFeedbackQueue().then(
        (items) => {
          // Only the newest response wins; an older one may be stale.
          if (alive && mine === seq) useNavStore.getState().setFeedbackBadge(items.length);
        },
        () => undefined,
      );
    };
    const start = (): void => {
      if (timer === null) timer = setInterval(refresh, POLL_MS);
    };
    const stop = (): void => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };

    refresh();
    if (!document.hidden) start();

    const onVisibility = (): void => {
      if (document.hidden) {
        stop();
      } else {
        refresh();
        start();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    // A turn has finished when its run leaves the in-flight set.
    let running = inFlightRuns(useChatStore.getState().sessions);
    const unsubscribe = useChatStore.subscribe((state) => {
      const now = inFlightRuns(state.sessions);
      const finished = [...running].some((id) => !now.has(id));
      running = now;
      if (!finished) return;
      refresh();
      if (followUp !== null) clearTimeout(followUp);
      followUp = setTimeout(() => {
        followUp = null;
        refresh();
      }, FOLLOW_UP_MS);
    });

    return () => {
      alive = false;
      stop();
      if (followUp !== null) clearTimeout(followUp);
      document.removeEventListener("visibilitychange", onVisibility);
      unsubscribe();
    };
  }, []);
}
