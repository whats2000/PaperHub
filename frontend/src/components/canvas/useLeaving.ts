/** A list of drawn items plus those just removed, kept (flagged `leaving`)
 * while they fade out; reduced motion drops them at once. */
import { useEffect, useMemo, useState } from "react";

import { prefersReducedMotion } from "@/lib/motion";

const LEAVE_MS = 200;

export function useLeaving<T extends { id: string }>(items: T[]): (T & { leaving?: boolean })[] {
  const [state, setState] = useState<{ items: T[]; leaving: T[] }>({ items, leaving: [] });
  // An item gone from the list stays, fading, for the exit (adjusted while
  // rendering: the list changed).
  if (state.items !== items) {
    const ids = new Set(items.map((i) => i.id));
    const gone = prefersReducedMotion() ? [] : state.items.filter((p) => !ids.has(p.id));
    setState({ items, leaving: [...state.leaving.filter((x) => !ids.has(x.id)), ...gone] });
  }
  const { leaving } = state;
  useEffect(() => {
    if (!leaving.length) return;
    const timer = setTimeout(
      () => setState((s) => ({ ...s, leaving: s.leaving.filter((x) => !leaving.includes(x)) })),
      LEAVE_MS,
    );
    return () => clearTimeout(timer);
  }, [leaving]);
  return useMemo(() => [...items, ...leaving.map((l) => ({ ...l, leaving: true }))], [items, leaving]);
}
