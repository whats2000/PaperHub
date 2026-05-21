import { render, screen, act, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasResize } from "@/hooks/useCanvasResize";
import {
  useCanvasStore,
  CANVAS_DEFAULT_WIDTH,
  CANVAS_MIN_WIDTH,
} from "@/store/canvas";

function Harness() {
  const { width, resizing, onPointerDown } = useCanvasResize();
  return (
    <div>
      <span data-testid="w">{width}</span>
      <span data-testid="r">{String(resizing)}</span>
      <div
        data-testid="handle"
        onPointerDown={onPointerDown}
        style={{ width: 6, height: 100 }}
      />
    </div>
  );
}

beforeEach(() => useCanvasStore.setState({ width: CANVAS_DEFAULT_WIDTH }));

// jsdom defaults window.innerWidth to 1024 → max width = 819.
describe("useCanvasResize", () => {
  it("widens when dragging the divider left", () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByTestId("handle"), { clientX: 600 });
    expect(screen.getByTestId("r").textContent).toBe("true");
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 500 }));
    });
    // dragged 100px left → +100
    expect(Number(screen.getByTestId("w").textContent)).toBe(
      CANVAS_DEFAULT_WIDTH + 100,
    );
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", {}));
    });
    expect(screen.getByTestId("r").textContent).toBe("false");
  });

  it("clamps to the minimum width when dragged far right", () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByTestId("handle"), { clientX: 600 });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 2000 }));
    });
    expect(Number(screen.getByTestId("w").textContent)).toBe(CANVAS_MIN_WIDTH);
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", {}));
    });
  });

  it("stops resizing after pointerup (no further width change)", () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByTestId("handle"), { clientX: 600 });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup", {}));
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 300 }));
    });
    // listener removed on pointerup → width unchanged from default
    expect(Number(screen.getByTestId("w").textContent)).toBe(
      CANVAS_DEFAULT_WIDTH,
    );
  });
});
