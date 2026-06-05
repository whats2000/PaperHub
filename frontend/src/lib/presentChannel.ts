/** Messages exchanged between the presenter cockpit and the audience window
 *  over BroadcastChannel('paperhub-present-<sid>'). */
export type PresentMessage =
  | { type: "page"; page: number }
  | { type: "ping" }
  | { type: "pong" };

/** One channel per session. Each `onX` registration holds a SINGLE callback —
 *  calling it again replaces the previous one (this is a single-consumer
 *  presenter↔audience topology, not a multi-subscriber bus). */
export interface PresentChannel {
  /** Presenter → audience: show this 1-indexed page. */
  postPage: (page: number) => void;
  onPage: (cb: (page: number) => void) => void;
  /** Presenter → audience heartbeat. */
  ping: () => void;
  onPing: (cb: () => void) => void;
  /** Audience → presenter heartbeat reply. */
  pong: () => void;
  onPong: (cb: () => void) => void;
  close: () => void;
}

export function presentChannelName(sessionId: number): string {
  return `paperhub-present-${sessionId}`;
}

/** Wrap a BroadcastChannel for one session. Same-origin only — the presenter
 *  cockpit and the audience window are both served from the app origin and run
 *  on the same machine (one projector); this is NOT a cross-device transport. */
export function createPresentChannel(sessionId: number): PresentChannel {
  const ch = new BroadcastChannel(presentChannelName(sessionId));
  let pageCb: ((p: number) => void) | null = null;
  let pingCb: (() => void) | null = null;
  let pongCb: (() => void) | null = null;
  ch.onmessage = (e: MessageEvent<PresentMessage>) => {
    const msg = e.data;
    if (msg?.type === "page" && typeof msg.page === "number") pageCb?.(msg.page);
    else if (msg?.type === "ping") pingCb?.();
    else if (msg?.type === "pong") pongCb?.();
  };
  return {
    postPage: (page) => ch.postMessage({ type: "page", page }),
    onPage: (cb) => {
      pageCb = cb;
    },
    ping: () => ch.postMessage({ type: "ping" }),
    onPing: (cb) => {
      pingCb = cb;
    },
    pong: () => ch.postMessage({ type: "pong" }),
    onPong: (cb) => {
      pongCb = cb;
    },
    close: () => ch.close(),
  };
}
