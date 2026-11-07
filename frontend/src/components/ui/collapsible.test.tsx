import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Collapsible } from "./collapsible";

const original = window.matchMedia;
afterEach(() => {
  window.matchMedia = original;
});

describe("Collapsible", () => {
  it("closed content is inert and hidden from assistive tech", () => {
    const { container } = render(
      <Collapsible open={false}>
        <p>body</p>
      </Collapsible>,
    );
    const root = container.firstChild as HTMLElement;
    expect(root).toHaveAttribute("data-state", "closed");
    expect(root).toHaveAttribute("inert");
    expect(root).toHaveAttribute("aria-hidden", "true");
  });

  it("open content is reachable", () => {
    const { container } = render(
      <Collapsible open>
        <p>body</p>
      </Collapsible>,
    );
    const root = container.firstChild as HTMLElement;
    expect(root).toHaveAttribute("data-state", "open");
    expect(root).not.toHaveAttribute("inert");
    expect(root).not.toHaveAttribute("aria-hidden");
  });

  it("calls onExpanded after the open transition only", () => {
    const onExpanded = vi.fn();
    const { rerender, container } = render(
      <Collapsible open={false} onExpanded={onExpanded}>
        <p>body</p>
      </Collapsible>,
    );
    rerender(
      <Collapsible open onExpanded={onExpanded}>
        <p>body</p>
      </Collapsible>,
    );
    const root = container.firstChild as HTMLElement;
    fireEvent.transitionEnd(screen.getByText("body"), {
      propertyName: "grid-template-rows",
    });
    expect(onExpanded).not.toHaveBeenCalled(); // bubbled child event
    fireEvent.transitionEnd(root, { propertyName: "opacity" });
    expect(onExpanded).not.toHaveBeenCalled();
    fireEvent.transitionEnd(root, { propertyName: "grid-template-rows" });
    expect(onExpanded).toHaveBeenCalledTimes(1);
    expect(onExpanded).toHaveBeenCalledWith(root);

    rerender(
      <Collapsible open={false} onExpanded={onExpanded}>
        <p>body</p>
      </Collapsible>,
    );
    fireEvent.transitionEnd(root, { propertyName: "grid-template-rows" });
    expect(onExpanded).toHaveBeenCalledTimes(1);
  });

  it("fires onExpanded immediately under reduced motion", () => {
    window.matchMedia = ((q: string) => ({
      matches: true,
      media: q,
    })) as unknown as typeof window.matchMedia;
    const onExpanded = vi.fn();
    const { rerender } = render(
      <Collapsible open={false} onExpanded={onExpanded}>
        <p>body</p>
      </Collapsible>,
    );
    rerender(
      <Collapsible open onExpanded={onExpanded}>
        <p>body</p>
      </Collapsible>,
    );
    expect(onExpanded).toHaveBeenCalledTimes(1);
  });

  it("falls back to a timer when no transitionend arrives", () => {
    vi.useFakeTimers();
    try {
      const onExpanded = vi.fn();
      const { rerender } = render(
        <Collapsible open={false} onExpanded={onExpanded}>
          <p>body</p>
        </Collapsible>,
      );
      const set = (o: boolean) =>
        rerender(
          <Collapsible open={o} onExpanded={onExpanded}>
            <p>body</p>
          </Collapsible>,
        );
      set(true);
      act(() => {
        vi.advanceTimersByTime(250);
      });
      expect(onExpanded).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("fires only once when transitionend precedes the fallback timer", () => {
    vi.useFakeTimers();
    try {
      const onExpanded = vi.fn();
      const { rerender, container } = render(
        <Collapsible open={false} onExpanded={onExpanded}>
          <p>body</p>
        </Collapsible>,
      );
      const set = (o: boolean) =>
        rerender(
          <Collapsible open={o} onExpanded={onExpanded}>
            <p>body</p>
          </Collapsible>,
        );
      set(true);
      fireEvent.transitionEnd(container.firstChild as HTMLElement, {
        propertyName: "grid-template-rows",
      });
      act(() => {
        vi.advanceTimersByTime(500);
      });
      expect(onExpanded).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("closing before the fallback cancels it", () => {
    vi.useFakeTimers();
    try {
      const onExpanded = vi.fn();
      const { rerender } = render(
        <Collapsible open={false} onExpanded={onExpanded}>
          <p>body</p>
        </Collapsible>,
      );
      const set = (o: boolean) =>
        rerender(
          <Collapsible open={o} onExpanded={onExpanded}>
            <p>body</p>
          </Collapsible>,
        );
      set(true);
      set(false);
      act(() => {
        vi.advanceTimersByTime(500);
      });
      expect(onExpanded).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
