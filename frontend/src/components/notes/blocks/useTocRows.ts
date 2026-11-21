import { useEffect, useState } from "react";

import { prefersReducedMotion } from "@/lib/motion";
import type { OutlineEntry } from "@/lib/notes/outline";

/** How long a removed entry takes to close. */
export const TOC_EXIT_MS = 200;

export type TocRowState = "in" | "open" | "out";
export interface TocRow extends OutlineEntry {
  state: TocRowState;
}

/** The live entries with their motion state: a new one mounts "in" (closed)
 * and opens on the next frame; a removed one stays "out" (closing) until it
 * has left, at the place it had. With reduced motion entries just come and
 * go. */
export function mergeRows(prev: TocRow[], entries: OutlineEntry[], animate: boolean): TocRow[] {
  const before = new Map(prev.map((r) => [r.blockId, r]));
  const next: TocRow[] = entries.map((e) => ({
    ...e,
    state: !animate || before.has(e.blockId) ? "open" : "in",
  }));
  if (!animate) return next;
  const live = new Set(entries.map((e) => e.blockId));
  prev.forEach((row, i) => {
    if (live.has(row.blockId)) return;
    // Back at the place it had: after the closest earlier row still listed.
    let at = 0;
    for (let k = i - 1; k >= 0; k--) {
      const idx = next.findIndex((r) => r.blockId === prev[k]!.blockId);
      if (idx >= 0) {
        at = idx + 1;
        break;
      }
    }
    next.splice(at, 0, { ...row, state: "out" });
  });
  return next;
}

export function useTocRows(entries: OutlineEntry[]): TocRow[] {
  const [rows, setRows] = useState<TocRow[]>(() => entries.map((e) => ({ ...e, state: "open" })));

  // Adjust during render (React's pattern for state derived from props).
  const [seen, setSeen] = useState(entries);
  if (seen !== entries) {
    setSeen(entries);
    setRows(mergeRows(rows, entries, !prefersReducedMotion()));
  }

  useEffect(() => {
    if (rows.some((r) => r.state === "in")) {
      const frame = requestAnimationFrame(() =>
        setRows((prev) => prev.map((r) => (r.state === "in" ? { ...r, state: "open" } : r))),
      );
      return () => cancelAnimationFrame(frame);
    }
    if (rows.some((r) => r.state === "out")) {
      const timer = setTimeout(() => setRows((prev) => prev.filter((r) => r.state !== "out")), TOC_EXIT_MS);
      return () => clearTimeout(timer);
    }
  }, [rows]);

  return rows;
}
