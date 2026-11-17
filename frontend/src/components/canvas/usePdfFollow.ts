/** Follow on a PDF (Plan H5b.1): after each scroll settles, the section at
 * the top of the reader's view.
 *
 * With Marker blocks it is the section of the block crossing the top line,
 * else the nearest below (running heads and page numbers never count). A
 * page without blocks — the LaTeX-compiled PDF of an HTML paper — has its
 * first lines of text at the top located in the paper's reading text
 * (`POST …/locate`). Nothing is reported until a section is known, and a
 * miss keeps the last one. */
import { useCallback, useEffect, useRef, type RefObject } from "react";

import { locateText } from "@/lib/api";
import { linesBelow, PAGE } from "@/lib/marks/pdfGeometry";
import type { PaperPageBlock } from "@/types/notes";

/** How long the reader rests before the section being read is reported. */
const READING_SETTLE_MS = 150;
/** Blocks Follow never reports from (running heads, page numbers). */
const NOT_READING = new Set(["PageHeader", "PageFooter"]);

export function usePdfFollow({
  paperId,
  readerRef,
  blocks,
  blocksRef,
  onReadingHeading,
}: {
  /** Null: no Follow (the standalone reader). */
  paperId: number | null;
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
  const following = paperId !== null && onReadingHeading !== undefined;
  // Located lines: their section (null: not found), asked once each.
  const located = useRef(new Map<string, string | null>());
  const asking = useRef<string | null>(null);

  const report = useCallback(() => {
    const say = (section: string | null) => {
      if (section !== null) onReadingRef.current?.(section, []);
    };
    const root = readerRef.current;
    if (!root || paperId === null) return;
    const view = root.getBoundingClientRect();
    const line = view.top + 4;
    let best: string | null = null;
    let bestD = Infinity;
    let topPage: Element | null = null;
    for (const el of Array.from(root.querySelectorAll(PAGE))) {
      const r = el.getBoundingClientRect();
      if (r.bottom <= line || r.top >= view.bottom) continue;
      topPage ??= el;
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
    if (best !== null) {
      say(best);
      return;
    }
    // No Marker blocks on the page at the top: find its text in the paper.
    const topBlocks = topPage ? blocksRef.current[Number(topPage.getAttribute("data-page-number")) - 1] : undefined;
    if (!topPage || !topBlocks || topBlocks.length > 0) return;
    const text = linesBelow(topPage, line);
    if (!text) return;
    if (located.current.has(text)) {
      say(located.current.get(text)!);
      return;
    }
    asking.current = text;
    locateText(paperId, text).then(
      (res) => {
        const section = res.match?.section ?? null;
        located.current.set(text, section);
        if (asking.current === text) say(section);
      },
      () => undefined, // keep the last section; asked again on the next scroll
    );
  }, [readerRef, blocksRef, paperId]);

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
