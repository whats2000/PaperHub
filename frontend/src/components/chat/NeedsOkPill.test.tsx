import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NeedsOkPill } from "@/components/chat/NeedsOkPill";
import { useNavStore } from "@/store/nav";

type Entry = { target: Element; isIntersecting: boolean; boundingClientRect: { top: number } };
let observers: { cb: (e: Entry[]) => void; targets: Element[] }[] = [];

class FakeObserver {
  targets: Element[] = [];
  constructor(cb: (e: Entry[]) => void) {
    observers.push({ cb, targets: this.targets });
  }
  observe(el: Element) {
    this.targets.push(el);
  }
  unobserve() {}
  disconnect() {
    this.targets.length = 0;
  }
}

/** Report each waiting card as visible or not, above (top < 0) or below. */
function report(states: Record<string, { visible: boolean; top: number }>) {
  const obs = observers.at(-1)!;
  act(() =>
    obs.cb(
      obs.targets.map((t) => {
        const el = t as HTMLElement;
        const s = states[(el.dataset.heldId ?? el.dataset.actionId)!]!;
        return { target: t, isIntersecting: s.visible, boundingClientRect: { top: s.top } };
      }),
    ),
  );
}

function Harness({ ids }: { ids: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={ref} data-testid="scroller">
        {ids.map((id) =>
          id.startsWith("a") ? <div key={id} data-action-id={id} /> : <div key={id} data-held-id={id} />,
        )}
      </div>
      <NeedsOkPill scrollerRef={ref} cardIds={ids} />
    </div>
  );
}

beforeEach(() => {
  observers = [];
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  useNavStore.setState({ focusHeldId: null });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("NeedsOkPill", () => {
  it("stays hidden while the waiting card is in view", () => {
    render(<Harness ids={["h1"]} />);
    report({ h1: { visible: true, top: 100 } });
    expect(screen.queryByRole("button", { name: /Paused · waiting for your OK/ })).toBeNull();
  });

  it("points up at a card scrolled above, and jumps to it", async () => {
    render(<Harness ids={["h1"]} />);
    report({ h1: { visible: false, top: -400 } });
    const pill = screen.getByRole("button", { name: /Paused · waiting for your OK/ });
    expect(pill.querySelector(".lucide-arrow-up")).not.toBeNull();
    await userEvent.setup().click(pill);
    expect(useNavStore.getState().focusHeldId).toBe("h1");
  });

  it("points down at a card below the view", () => {
    render(<Harness ids={["h1"]} />);
    report({ h1: { visible: false, top: 4000 } });
    const pill = screen.getByRole("button", { name: /Paused · waiting for your OK/ });
    expect(pill.querySelector(".lucide-arrow-down")).not.toBeNull();
  });

  it("slides out when the card comes into view", () => {
    vi.useFakeTimers();
    render(<Harness ids={["h1"]} />);
    report({ h1: { visible: false, top: -400 } });
    const pill = screen.getByRole("button", { name: /Paused · waiting for your OK/ });
    expect(pill).toHaveAttribute("data-state", "open");
    report({ h1: { visible: true, top: 10 } });
    expect(pill).toHaveAttribute("data-state", "closed");
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.queryByRole("button", { name: /Paused · waiting for your OK/ })).toBeNull();
  });

  it("covers Ask-first action cards and counts every waiting card", async () => {
    render(<Harness ids={["h1", "a1"]} />);
    report({ h1: { visible: true, top: 100 }, a1: { visible: false, top: 4000 } });
    const pill = screen.getByRole("button", { name: /Paused · 2 items need your OK/ });
    expect(pill.querySelector(".lucide-arrow-down")).not.toBeNull();
    await userEvent.setup().click(pill);
    expect(useNavStore.getState().focusHeldId).toBe("a1");
  });

  it("shows nothing without a pending card", () => {
    render(<Harness ids={[]} />);
    expect(screen.queryByRole("button", { name: /Paused · waiting for your OK/ })).toBeNull();
  });
});
