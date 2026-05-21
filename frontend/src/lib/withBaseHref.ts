/**
 * Inject a `<base href>` into a rendered paper's HTML before it's embedded via
 * an iframe `srcdoc`.
 *
 * Why: a `srcdoc` document's base URL is the embedding app's origin, so the
 * paper's RELATIVE asset URLs (e.g. `asset/source/fig.png`, served by the
 * backend at `/papers/content/{id}/asset/...`) would resolve against the app
 * origin and 404. A `<base href>` pointing at the backend's per-paper root
 * makes those relative URLs resolve to the backend instead. (Absolute URLs —
 * the MathJax CDN script, `data:` inlined images — are unaffected.)
 *
 * The tag is inserted immediately after `<head>` so it precedes any
 * relative-URL element. Falls back to creating a head, or prepending, when the
 * markup lacks one.
 */
export function withBaseHref(html: string, baseHref: string): string {
  const baseTag = `<base href="${baseHref}">`;
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (m) => `${m}${baseTag}`);
  }
  if (/<html[^>]*>/i.test(html)) {
    return html.replace(/<html[^>]*>/i, (m) => `${m}<head>${baseTag}</head>`);
  }
  return `${baseTag}${html}`;
}
