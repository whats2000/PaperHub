import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HtmlView } from "@/components/canvas/HtmlView";
import { DARK_STYLE_ID } from "@/lib/applyIframeTheme";

const PAPER = "<!DOCTYPE html><html><head><title>p</title></head><body><p>Body</p></body></html>";

const view = (isDark: boolean, html = PAPER) => (
  <HtmlView
    html={html}
    isDark={isDark}
    highlightDomId={null}
    highlightText={null}
    sectionTitle={null}
    nonce={0}
  />
);
const srcdoc = (c: HTMLElement) => c.querySelector("iframe")?.getAttribute("srcdoc") ?? "";

describe("HtmlView dark first paint", () => {
  it("hands a dark paper to the iframe with the dark style already in it", () => {
    const { container } = render(view(true));
    expect(srcdoc(container)).toContain(`id="${DARK_STYLE_ID}"`);
    expect(srcdoc(container)).toContain("<p>Body</p>");
  });

  it("leaves a light paper untouched", () => {
    const { container } = render(view(false));
    expect(srcdoc(container)).toBe(PAPER);
  });

  it("does not reload the paper when the theme switches", () => {
    const { container, rerender } = render(view(true));
    const first = srcdoc(container);
    rerender(view(false));
    expect(srcdoc(container)).toBe(first);
  });

  it("takes the current theme for a new paper", () => {
    const { container, rerender } = render(view(true));
    rerender(view(false, PAPER.replace("Body", "Other")));
    expect(srcdoc(container)).not.toContain(DARK_STYLE_ID);
    expect(srcdoc(container)).toContain("Other");
  });
});

describe("HtmlView loading skeleton", () => {
  it("shows the skeleton until the paper has loaded, then fades it out", () => {
    vi.useFakeTimers();
    try {
      const { container } = render(view(false));
      expect(screen.getByRole("status")).toBeInTheDocument();
      act(() => {
        container.querySelector("iframe")!.dispatchEvent(new Event("load"));
      });
      act(() => {
        vi.advanceTimersByTime(400);
      });
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
