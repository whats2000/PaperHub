/** An id that is safe inside `url(#…)`: React's useId may contain `:` or `«»`. */
export function svgSafeId(raw: string): string {
  return raw.replace(/[^A-Za-z0-9_-]/g, "");
}
