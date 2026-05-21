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
/**
 * Remove dead / unnecessary external `<script>`s pandoc's MathJax template
 * injects, which stall the page load:
 * - `polyfill.io` — sold + served malware in 2024, now blocked/dead at most
 *   networks, so the browser hangs on it before timing out (and it's a
 *   supply-chain risk). Modern browsers don't need it.
 * - `html5shiv` — an old-IE shim, irrelevant in modern browsers.
 * MathJax itself (the CDN script that actually typesets math) is left intact.
 */
export function stripDeadCdnScripts(html: string): string {
  return html.replace(
    /<script\b[^>]*\bsrc="[^"]*(?:polyfill\.io|html5shiv)[^"]*"[^>]*>\s*<\/script>/gi,
    "",
  );
}

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
