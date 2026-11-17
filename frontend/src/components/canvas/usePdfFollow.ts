/** Follow on a PDF (Plan H5b.1): after each scroll settles, the section of
 * the Marker block at the top of the reader's view — the block crossing
 * the top line, else the nearest below (running heads and page numbers
 * never count). Nothing is reported until a section is known. */
import { useCallback, useEffect, useRef, type RefObject } from "react";

import { PAGE } from "@/lib/marks/pdfGeometry";
import type { PaperPageBlock } from "@/types/notes";

/** How long the reader rests before the section being read is reported. */
const READING_SETTLE_MS = 150;
/** Blocks Follow never reports from (running heads, page numbers). */
const NOT_READING = new Set(["PageHeader", "PageFooter"]);

export function usePdfFollow({
  enabled,
  readerRef,
  blocks,
  blocksRef,
  onReadingHeading,
}: {
  enabled: boolean;
  readerRef: RefObject<HTMLElement | null>;
  /** The loaded blocks (a change reports again). */
  blocks: Readonly<Record<number, PaperPageBlock[]>>;
  blocksRef: RefObject<Readonly<Record<number, PaperPageBlock[]>>>;
  onReadingHeading?: (heading: string | null, within: string[]) => void;
}): void {
  const onReadingRef = useRef(onReadingHeading);
  useEffect(() => {
    onReadingRef.current = onReadingHeading;
  }, [onReadingHeading]);
  const following = enabled && onReadingHeading !== undefined;

  const report = useCallback(() => {
    const root = readerRef.current;
    if (!root) return;
    const view = root.getBoundingClientRect();
    const line = view.top + 4;
    let best: string | null = null;
    let bestD = Infinity;
    for (const el of Array.from(root.querySelectorAll(PAGE))) {
      const r = el.getBoundingClientRect();
      if (r.bottom <= line || r.top >= view.bottom) continue;
      const list = blocksRef.current[Number(el.getAttribute("data-page-number")) - 1];
      for (const b of list ?? []) {
        if (!b.section || NOT_READING.has(b.type)) continue;
        const top = r.top + b.box[1] * r.height;
        const bottom = r.top + b.box[3] * r.height;
        if (bottom <= line) continue;
        const d = top <= line ? 0 : top - line;
        if (d < bestD) {
          bestD = d;
          best = b.section;
        }
      }
    }
    if (best !== null) onReadingRef.current?.(best, []);
  }, [readerRef, blocksRef]);

  useEffect(() => {
    const root = readerRef.current;
    if (!following || !root) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const settle = () => {
      clearTimeout(timer);
      timer = setTimeout(report, READING_SETTLE_MS);
    };
    settle();
    root.addEventListener("scroll", settle, { passive: true });
    window.addEventListener("resize", settle);
    return () => {
      clearTimeout(timer);
      root.removeEventListener("scroll", settle);
      window.removeEventListener("resize", settle);
    };
  }, [following, readerRef, report, blocks]);
}
