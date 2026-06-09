import { describe, expect, it } from "vitest";
import {
  withBaseHref,
  stripDeadCdnScripts,
  injectPerfStyle,
  localizeMathjax,
  MATHJAX_PATH,
} from "@/lib/withBaseHref";

const ORIGIN = "http://localhost:5173";
const VENDORED = `${ORIGIN}${MATHJAX_PATH}`;

const BASE = "http://localhost:8000/papers/content/7/";

describe("injectPerfStyle", () => {
  it("injects content-visibility into an existing <head>", () => {
    const out = injectPerfStyle(
      "<!DOCTYPE html><html><head><title>x</title></head><body><p>hi</p></body></html>",
    );
    expect(out).toContain("content-visibility: auto");
    expect(out).toContain("contain-intrinsic-size");
    // Inside <head>, before the body content.
    expect(out.indexOf("content-visibility")).toBeLessThan(out.indexOf("<body"));
  });

  it("creates a <head> when the markup lacks one", () => {
    const out = injectPerfStyle("<html><body><p>hi</p></body></html>");
    expect(out).toContain("content-visibility: auto");
    expect(out).toContain("<head>");
  });

  it("falls back to prepending when there is no <html>", () => {
    const out = injectPerfStyle("<p>bare fragment</p>");
    expect(out.startsWith("<style>")).toBe(true);
    expect(out).toContain("bare fragment");
  });

  it("hints figures are clickable with a zoom-in cursor", () => {
    const out = injectPerfStyle("<html><head></head><body></body></html>");
    expect(out).toContain("cursor: zoom-in");
  });
});

describe("withBaseHref", () => {
  it("inserts <base> right after an existing <head>", () => {
    const out = withBaseHref(
      "<!DOCTYPE html><html><head><title>x</title></head><body></body></html>",
      BASE,
    );
    expect(out).toContain(`<head><base href="${BASE}">`);
    // base precedes the title (so relative URLs resolve correctly)
    expect(out.indexOf("<base")).toBeLessThan(out.indexOf("<title"));
  });

  it("creates a <head> when there's only <html>", () => {
    const out = withBaseHref("<html><body><img src='asset/f.png'></body></html>", BASE);
    expect(out).toContain(`<head><base href="${BASE}"></head>`);
  });

  it("prepends the tag when there's no head or html", () => {
    const out = withBaseHref("<p>hi</p>", BASE);
    expect(out.startsWith(`<base href="${BASE}">`)).toBe(true);
  });

  it("strips the dead polyfill.io + html5shiv scripts but keeps MathJax", () => {
    const html =
      '<head>' +
      '<script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>' +
      '<script src="//cdnjs.cloudflare.com/ajax/libs/html5shiv/3.7.3/html5shiv-printshiv.min.js"></script>' +
      '<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>' +
      "</head>";
    const out = stripDeadCdnScripts(html);
    expect(out).not.toContain("polyfill.io");
    expect(out).not.toContain("html5shiv");
    expect(out).toContain("mathjax"); // the math typesetter is preserved
  });

  it("strips even when a removed match would re-form a new one (loops to fixed point)", () => {
    // Overlapping/adjacent tags where a single pass could leave a reconstructed
    // match behind — the apply-until-stable loop must remove all of them.
    const dead =
      '<script src="https://polyfill.io/v3/polyfill.min.js"></script>';
    const html = `<head>${dead}${dead}<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script></head>`;
    const out = stripDeadCdnScripts(html);
    expect(out).not.toContain("polyfill.io");
    expect(out).toContain("mathjax");
  });

  it("repoints a Debian local MathJax path at the vendored build, preserving attrs", () => {
    // Debian's pandoc patches --mathjax to this local path; in the iframe it
    // resolves to the app origin's SPA fallback (HTML) and math never typesets.
    const html =
      '<head><script src="/usr/share/javascript/mathjax/MathJax.js" type="text/javascript"></script></head>';
    const out = localizeMathjax(html, ORIGIN);
    expect(out).not.toContain("/usr/share/javascript/mathjax/MathJax.js");
    expect(out).toContain(`src="${VENDORED}"`);
    expect(out).toContain('type="text/javascript"');
  });

  it("repoints the local path even with a ?config query (MathJax 2 form)", () => {
    const html =
      '<script src="/usr/share/javascript/mathjax/MathJax.js?config=TeX-AMS_HTML"></script>';
    expect(localizeMathjax(html, ORIGIN)).toContain(`src="${VENDORED}"`);
    expect(localizeMathjax(html, ORIGIN)).not.toContain("config=TeX-AMS_HTML");
  });

  it("repoints a jsdelivr CDN MathJax tag at the vendored build (offline-capable)", () => {
    const html =
      '<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js"></script>';
    const out = localizeMathjax(html, ORIGIN);
    expect(out).toContain(`src="${VENDORED}"`);
    expect(out).not.toContain("cdn.jsdelivr.net");
  });

  it("uses an ABSOLUTE frontend-origin URL (immune to the iframe's backend <base href>)", () => {
    // Dev sets <base href="http://localhost:8000/..."> (the backend). A root-
    // relative /vendor/... would resolve against the backend and 404; the
    // injected URL must carry the frontend origin so it hits the app that
    // serves /vendor/.
    const html =
      '<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js"></script>';
    const out = localizeMathjax(html, "http://localhost:5173");
    expect(out).toContain('src="http://localhost:5173/vendor/mathjax/tex-chtml-full.js"');
  });

  it("is idempotent on an already-vendored tag", () => {
    const html = `<script src="${VENDORED}"></script>`;
    expect(localizeMathjax(html, ORIGIN)).toBe(html);
  });

  it("never touches the inline window.MathJax config (no src)", () => {
    const html = "<script>window.MathJax={tex:{macros:{}}};</script>";
    expect(localizeMathjax(html, ORIGIN)).toBe(html);
  });

  it("makes a relative asset URL resolve against the backend (DOMParser check)", () => {
    const html = withBaseHref(
      "<html><head></head><body><img src='asset/source/fig.png'></body></html>",
      BASE,
    );
    const doc = new DOMParser().parseFromString(html, "text/html");
    const img = doc.querySelector("img");
    // jsdom resolves img.src against the document's <base href>.
    expect(img?.src).toBe(`${BASE}asset/source/fig.png`);
  });
});
