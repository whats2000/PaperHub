import { useCallback, useEffect, useLayoutEffect, type RefObject } from "react";

/** The tallest the box grows, as a share of the viewport height. */
const MAX_VIEWPORT_SHARE = 0.4;

/**
 * Grow a textarea with its content (never below its `rows`) up to 40% of
 * the viewport, then scroll inside it. Re-measures on every value change
 * (typing, a prefilled message, clearing after send) and on window resize.
 * No height animation: the box tracks typing immediately.
 */
export function useAutoGrowTextarea(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
): void {
  const resize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto"; // collapse to `rows` so scrollHeight is the content
    const max = Math.round(window.innerHeight * MAX_VIEWPORT_SHARE);
    const content = el.scrollHeight;
    el.style.height = `${Math.min(content, max)}px`;
    el.style.overflowY = content > max ? "auto" : "hidden";
  }, [ref]);

  useLayoutEffect(resize, [resize, value]);

  useEffect(() => {
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [resize]);
}
