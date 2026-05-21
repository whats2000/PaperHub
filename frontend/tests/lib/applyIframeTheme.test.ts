import { describe, expect, it } from "vitest";
import { applyIframeTheme, DARK_STYLE_ID } from "@/lib/applyIframeTheme";

function docFrom(body = "<p>hi</p>"): Document {
  return new DOMParser().parseFromString(
    `<!DOCTYPE html><html><head></head><body>${body}</body></html>`,
    "text/html",
  );
}

describe("applyIframeTheme", () => {
  it("injects an inversion stylesheet when dark", () => {
    const doc = docFrom();
    applyIframeTheme(doc, true);
    const style = doc.getElementById(DARK_STYLE_ID);
    expect(style).not.toBeNull();
    expect(style?.textContent).toMatch(/invert/);
  });

  it("removes the stylesheet when light", () => {
    const doc = docFrom();
    applyIframeTheme(doc, true);
    applyIframeTheme(doc, false);
    expect(doc.getElementById(DARK_STYLE_ID)).toBeNull();
  });

  it("is idempotent — only one style node when applied twice", () => {
    const doc = docFrom();
    applyIframeTheme(doc, true);
    applyIframeTheme(doc, true);
    expect(doc.querySelectorAll(`#${DARK_STYLE_ID}`).length).toBe(1);
  });

  it("does nothing harmful when light and no style exists", () => {
    const doc = docFrom();
    expect(() => applyIframeTheme(doc, false)).not.toThrow();
    expect(doc.getElementById(DARK_STYLE_ID)).toBeNull();
  });
});
