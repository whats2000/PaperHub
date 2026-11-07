/** True when the user asked the OS/browser for reduced motion. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Scroll `el` into view, smoothly unless reduced motion is requested. */
export function revealInScroll(
  el: Element,
  block: "nearest" | "end" = "nearest",
): void {
  el.scrollIntoView({
    block,
    behavior: prefersReducedMotion() ? "auto" : "smooth",
  });
}
