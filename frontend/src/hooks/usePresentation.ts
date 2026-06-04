import { useCallback, useEffect, useRef, useState } from "react";

import { useSlidesStore } from "@/store/slides";
import { createPresentChannel, type PresentChannel } from "@/lib/presentChannel";

const HEARTBEAT_MS = 1000;
const STALE_MS = 2500;

interface Options {
  /** Injectable for tests; defaults to window.open. */
  openWindow?: (url: string, target: string, features: string) => Window | null;
}

export interface Presentation {
  presenting: boolean;
  audienceConnected: boolean;
  present: () => void;
  stop: () => void;
}

/**
 * Owns the BroadcastChannel for one session's presentation. The channel is
 * (re)created whenever `presenting` is true and none exists — so after the
 * Slides panel unmounts/remounts for a Q&A turn, reopening it reconnects to the
 * still-open audience window without reopening it. `presenting` lives in the
 * store, so it survives that remount.
 */
export function usePresentation(
  sessionId: number,
  currentPage: number,
  opts: Options = {},
): Presentation {
  // Stable ref for the injectable; updated in an effect so `present` doesn't
  // re-create on every render when the caller passes an inline function.
  const openWindowRef = useRef(
    opts.openWindow ?? ((u: string, t: string, f: string) => window.open(u, t, f)),
  );
  useEffect(() => {
    openWindowRef.current =
      opts.openWindow ?? ((u: string, t: string, f: string) => window.open(u, t, f));
  });

  const presenting = useSlidesStore(
    (s) => s.presentingBySession[sessionId] ?? false,
  );
  const startPresenting = useSlidesStore((s) => s.startPresenting);
  const stopPresenting = useSlidesStore((s) => s.stopPresenting);

  const channelRef = useRef<PresentChannel | null>(null);
  const lastPongRef = useRef(0);
  const pageRef = useRef(currentPage);
  // Keep pageRef current via an effect, not during render, to satisfy the
  // react-hooks/refs lint rule (refs must not be mutated during render).
  useEffect(() => {
    pageRef.current = currentPage;
  });
  const [audienceConnected, setAudienceConnected] = useState(false);

  const present = useCallback(() => {
    startPresenting(sessionId);
    openWindowRef.current(
      `/present.html?session=${sessionId}`,
      `paperhub-present-${sessionId}`,
      "popup,width=1280,height=800",
    );
    // The channel is created by the effect below when `presenting` flips true.
  }, [sessionId, startPresenting]);

  const stop = useCallback(() => {
    channelRef.current?.close();
    channelRef.current = null;
    setAudienceConnected(false);
    stopPresenting(sessionId);
  }, [sessionId, stopPresenting]);

  // (Re)create the channel whenever presenting and none exists. Covers both the
  // initial present() and a panel remount during Q&A.
  useEffect(() => {
    if (presenting && !channelRef.current) {
      const ch = createPresentChannel(sessionId);
      ch.onPong(() => {
        lastPongRef.current = Date.now();
      });
      channelRef.current = ch;
      ch.postPage(pageRef.current);
    }
  }, [presenting, sessionId]);

  // Broadcast page changes while presenting.
  useEffect(() => {
    if (presenting) channelRef.current?.postPage(currentPage);
  }, [presenting, currentPage]);

  // Heartbeat: ping the audience; mark connected while pongs stay fresh.
  useEffect(() => {
    if (!presenting) return;
    const id = setInterval(() => {
      channelRef.current?.ping();
      setAudienceConnected(Date.now() - lastPongRef.current < STALE_MS);
    }, HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [presenting]);

  // Close the channel on unmount (it is recreated by the effect above on a
  // remount if still presenting). Does NOT stop presenting — Q&A reopen resumes.
  useEffect(
    () => () => {
      channelRef.current?.close();
      channelRef.current = null;
    },
    [],
  );

  return { presenting, audienceConnected, present, stop };
}
