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

/**
 * Inject a `content-visibility: auto` hint on the paper's top-level blocks.
 *
 * Why: the Citation Canvas embeds a full paper via an iframe `srcdoc`. When the
 * canvas is revealed (`display:none → block`), the browser lays out the ENTIRE
 * document at once — for a long, equation-heavy paper that's a multi-second
 * main-thread freeze, and it recurs on every open/close toggle. `content-
 * visibility: auto` lets the browser skip layout + paint for off-screen blocks,
 * so a reveal only lays out the visible screenful; the rest renders lazily as it
 * scrolls into view. `contain-intrinsic-size` supplies a placeholder size so the
 * scroll height stays stable for not-yet-rendered blocks.
 *
 * BUT a skipped block reports that 600px PLACEHOLDER instead of its real height,
 * which throws off `scrollIntoView` offsets. We mitigate the worst case by
 * EXCLUDING image-bearing blocks (`:not(:has(img))`) so figures — whose height
 * deviates most from 600px — keep full layout; text/math blocks (the heavy,
 * predictable-height ones the freeze was about) still render lazily. The
 * residual drift from lazy blocks is handled at scroll time by the re-targeting
 * glide in `findAndHighlight.scrollIntoViewStable`, which tracks the target as
 * blocks render. `img { max-width:100%; height:auto }` keeps figures scaled to
 * the iframe width.
 *
 * Injected into the HTML string (so it's in the `srcdoc` from parse time, before
 * the first layout) rather than via JS after load, which would be too late.
 */
const PERF_STYLE =
  "<style>" +
  "body > *:not(:has(img)) { content-visibility: auto; contain-intrinsic-size: auto 600px; }" +
  "img { max-width: 100%; height: auto; }" +
  "</style>";

export function injectPerfStyle(html: string): string {
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (m) => `${m}${PERF_STYLE}`);
  }
  if (/<html[^>]*>/i.test(html)) {
    return html.replace(/<html[^>]*>/i, (m) => `${m}<head>${PERF_STYLE}</head>`);
  }
  return `${PERF_STYLE}${html}`;
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
