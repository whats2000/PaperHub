import { translateBlocks } from "@/lib/api";
import type {
  TranslateRequest,
  TranslateResponse,
  TranslateResult,
} from "@/types/translation";

/** Screens' worth of look-ahead, in blocks, after the last visible block. */
const LOOKAHEAD = 30;
/** Consecutive all-unavailable batches that pause the full-paper background. */
const BACKGROUND_FAIL_LIMIT = 3;

export interface TranslationQueueOptions {
  paperId: number;
  language: string;
  /** Translate the whole paper (server config), not just the look-ahead window. */
  fullPaper: boolean;
  send?: (req: TranslateRequest) => Promise<TranslateResponse>;
  maxInFlight?: number;
  batchSize?: number;
}

/** What the reader may want to act on: keys whose last result was
 *  `unavailable`, and whether the full-paper background run is paused. */
export interface TranslationQueueStatus {
  failed: number;
  paused: boolean;
}

export interface TranslationQueue {
  setBlocks: (keysInOrder: string[], textByKey: Record<string, string>) => void;
  setVisible: (keys: string[]) => void;
  onResult: (cb: (r: TranslateResult) => void) => () => void;
  /** Release keys so they can be requested again (e.g. after `unavailable`). */
  retry: (keys: string[]) => void;
  /** Release every key whose last result was `unavailable` (and resume a
   *  paused background run); returns the released keys. */
  retryFailed: () => string[];
  /** Called whenever the failed count or the paused flag changes. */
  onStatus: (cb: (s: TranslationQueueStatus) => void) => () => void;
  /** Re-send keys with `force: true` (re-translate with current terminology),
   *  ahead of other work. If the forced request fails, the key's last good
   *  result is re-emitted, flagged `stale_terminology`, so text never blanks. */
  retranslate: (keys: string[]) => void;
  dispose: () => void;
}

/** An `unavailable` result (including a thrown request) is terminal: the key
 *  stays claimed and is never re-sent until the consumer calls `retry(keys)`. */
/** Reading-position-ordered request queue: visible blocks, then the next
 *  LOOKAHEAD blocks, then (fullPaper only) the rest, paused after
 *  BACKGROUND_FAIL_LIMIT all-unavailable batches in a row. Requests carry <= batchSize
 *  adjacent keys; at most maxInFlight run at once. Every pump recomputes the
 *  order from the current visible set, so a scroll re-prioritizes unsent work. */
export function createTranslationQueue(
  opts: TranslationQueueOptions,
): TranslationQueue {
  const {
    paperId,
    language,
    fullPaper,
    send = translateBlocks,
    maxInFlight = 2,
    batchSize = 5,
  } = opts;

  let order: string[] = [];
  let index = new Map<string, number>();
  let text: Record<string, string> = {};
  let visible: string[] = [];
  const claimed = new Set<string>(); // sent or in flight
  const forced = new Set<string>(); // released for one forced re-send
  const lastOk = new Map<string, TranslateResult>();
  // Request sequence, and per key the last sequence sent before a retranslate:
  // an unforced reply from such a request is superseded and dropped.
  let seq = 0;
  const forcedAfter = new Map<string, number>();
  const listeners = new Set<(r: TranslateResult) => void>();
  const statusListeners = new Set<(s: TranslationQueueStatus) => void>();
  // Keys whose last emitted result was unavailable.
  const failed = new Set<string>();
  let lastStatus: TranslationQueueStatus = { failed: 0, paused: false };
  let inFlight = 0;
  let disposed = false;
  // Consecutive batches whose every key came back unavailable (or threw).
  // At the limit the full-paper background pauses until retry() or a
  // visible-set change, so a down provider isn't hammered for the whole paper.
  let failStreak = 0;

  /** Pending keys in priority order, with the eligible set. */
  const plan = (): { ranked: string[]; eligible: Set<string> } => {
    const ranked: string[] = [];
    const seen = new Set<string>();
    const add = (k: string) => {
      if (!seen.has(k) && index.has(k) && !claimed.has(k) && text[k]) {
        seen.add(k);
        ranked.push(k);
      }
    };
    forced.forEach(add);
    const vis = visible
      .filter((k) => index.has(k))
      .sort((a, b) => index.get(a)! - index.get(b)!);
    vis.forEach(add);
    if (vis.length) {
      const last = index.get(vis[vis.length - 1]!)!;
      for (let i = last + 1; i <= last + LOOKAHEAD && i < order.length; i++) {
        add(order[i]!);
      }
    }
    if (fullPaper && failStreak < BACKGROUND_FAIL_LIMIT) order.forEach(add);
    return { ranked, eligible: seen };
  };

  const pump = () => {
    while (!disposed && inFlight < maxInFlight) {
      const { ranked, eligible } = plan();
      if (!ranked.length) return;
      const start = index.get(ranked[0]!)!;
      // A batch is all forced or all unforced: `force` is per request.
      const force = forced.has(ranked[0]!);
      const batch: string[] = [ranked[0]!];
      for (
        let i = start + 1;
        batch.length < batchSize &&
        i < order.length &&
        eligible.has(order[i]!) &&
        forced.has(order[i]!) === force;
        i++
      ) {
        batch.push(order[i]!);
      }
      batch.forEach((k) => {
        claimed.add(k);
        forced.delete(k);
      });
      inFlight++;
      void run(batch, force);
    }
  };

  const isPaused = () =>
    fullPaper && failStreak >= BACKGROUND_FAIL_LIMIT && order.some((k) => !claimed.has(k));
  const reportStatus = () => {
    if (disposed) return;
    const next = { failed: failed.size, paused: isPaused() };
    if (next.failed === lastStatus.failed && next.paused === lastStatus.paused) return;
    lastStatus = next;
    statusListeners.forEach((cb) => cb(next));
  };

  const emit = (r: TranslateResult) => {
    if (disposed) return;
    if (r.status === "ok" && r.text !== null) failed.delete(r.key);
    else failed.add(r.key);
    listeners.forEach((cb) => {
      try {
        cb(r);
      } catch (e) {
        console.error("translation onResult listener threw", e);
      }
    });
  };

  const unavailable = (key: string): TranslateResult => ({
    key,
    status: "unavailable",
    text: null,
    model: null,
    stale_terminology: false,
  });

  /** A failed forced re-send falls back to the last good text, still stale. */
  const settle = (r: TranslateResult, force: boolean): TranslateResult => {
    if (r.status === "ok" && r.text !== null) {
      lastOk.set(r.key, r);
      return r;
    }
    const prev = force ? lastOk.get(r.key) : undefined;
    return prev ? { ...prev, stale_terminology: true } : r;
  };

  const run = async (keys: string[], force: boolean) => {
    const id = ++seq;
    const superseded = (k: string) => !force && id <= (forcedAfter.get(k) ?? 0);
    let results: TranslateResult[];
    try {
      const res = await send({
        paper_content_id: paperId,
        language,
        ...(force ? { force: true } : {}),
        blocks: keys.map((key) => ({ key, text: text[key] ?? "" })),
      });
      results = res.results;
    } catch {
      results = [];
    }
    inFlight--;
    const anyOk = results.some((r) => r.status === "ok" && r.text !== null);
    failStreak = anyOk ? 0 : failStreak + 1;
    results.filter((r) => !superseded(r.key)).forEach((r) => emit(settle(r, force)));
    const got = new Set(results.map((r) => r.key));
    keys
      .filter((k) => !got.has(k) && !superseded(k))
      .forEach((k) => emit(settle(unavailable(k), force)));
    pump();
    reportStatus();
  };

  return {
    setBlocks(keysInOrder, textByKey) {
      // Already-claimed keys are not re-sent if their text changes here.
      order = keysInOrder;
      index = new Map(order.map((k, i) => [k, i]));
      text = textByKey;
      pump();
    },
    setVisible(keys) {
      if (keys.length !== visible.length || keys.some((k, i) => k !== visible[i])) {
        failStreak = 0;
      }
      visible = keys;
      pump();
      reportStatus();
    },
    retry(keys) {
      failStreak = 0;
      keys.forEach((k) => claimed.delete(k));
      pump();
      reportStatus();
    },
    retryFailed() {
      const keys = [...failed];
      failed.clear();
      failStreak = 0;
      keys.forEach((k) => claimed.delete(k));
      pump();
      reportStatus();
      return keys;
    },
    onStatus(cb) {
      statusListeners.add(cb);
      return () => statusListeners.delete(cb);
    },
    retranslate(keys) {
      keys.forEach((k) => {
        claimed.delete(k);
        forced.add(k);
        forcedAfter.set(k, seq);
      });
      pump();
    },
    onResult(cb) {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    dispose() {
      disposed = true;
      listeners.clear();
      statusListeners.clear();
    },
  };
}
