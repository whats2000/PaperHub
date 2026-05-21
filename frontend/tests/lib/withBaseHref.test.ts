import { describe, expect, it } from "vitest";
import { withBaseHref } from "@/lib/withBaseHref";

const BASE = "http://localhost:8000/papers/content/7/";

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
