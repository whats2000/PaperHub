import { useRef } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useAutoGrowTextarea } from "./useAutoGrowTextarea";

// jsdom has no layout: derive scrollHeight from the line count (20px a line).
let restore: (() => void) | null = null;
beforeEach(() => {
  const desc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
  Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: HTMLTextAreaElement) {
      return Math.max(2, this.value.split("\n").length) * 20;
    },
  });
  window.innerHeight = 1000; // max = 40% = 400px
  restore = () => {
    if (desc) Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", desc);
    else delete (HTMLTextAreaElement.prototype as { scrollHeight?: number }).scrollHeight;
  };
});
afterEach(() => restore?.());

function Box({ value }: { value: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useAutoGrowTextarea(ref, value);
  return <textarea aria-label="box" ref={ref} value={value} readOnly rows={2} />;
}

const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i}`).join("\n");

describe("useAutoGrowTextarea", () => {
  it("sizes a prefilled message to its full content", () => {
    render(<Box value={lines(6)} />);
    const box = screen.getByLabelText("box");
    expect(box.style.height).toBe("120px");
    expect(box.style.overflowY).toBe("hidden");
  });

  it("caps the height at 40% of the viewport and scrolls inside beyond it", () => {
    render(<Box value={lines(50)} />);
    const box = screen.getByLabelText("box");
    expect(box.style.height).toBe("400px");
    expect(box.style.overflowY).toBe("auto");
  });

  it("shrinks back when the text is cleared", () => {
    const { rerender } = render(<Box value={lines(6)} />);
    const box = screen.getByLabelText("box");
    rerender(<Box value="" />);
    expect(box.style.height).toBe("40px");
  });
});
