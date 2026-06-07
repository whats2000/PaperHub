import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImageLightbox } from "@/components/canvas/ImageLightbox";

const SRC = "http://backend.test/papers/content/7/asset/fig1.png";

describe("ImageLightbox", () => {
  it("renders the figure as an accessible modal dialog", () => {
    render(<ImageLightbox src={SRC} alt="Architecture diagram" onClose={vi.fn()} />);

    const dialog = screen.getByRole("dialog", {
      name: /image preview: architecture diagram/i,
    });
    expect(dialog).toBeInTheDocument();

    const img = screen.getByAltText("Architecture diagram");
    expect(img).toHaveAttribute("src", SRC);
  });

  it("exposes zoom in / out / fit controls", () => {
    render(<ImageLightbox src={SRC} alt="fig" onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: /zoom in/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /zoom out/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reset zoom/i })).toBeInTheDocument();
  });

  it("closes when the close button is clicked", async () => {
    const onClose = vi.fn();
    render(<ImageLightbox src={SRC} alt="fig" onClose={onClose} />);

    await userEvent.click(
      screen.getByRole("button", { name: /close image preview/i }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("double-clicking the figure zooms instead of dismissing", () => {
    const onClose = vi.fn();
    render(<ImageLightbox src={SRC} alt="fig" onClose={onClose} />);

    const dialog = screen.getByRole("dialog");
    screen.getByAltText("fig").getBoundingClientRect = (): DOMRect => ({
      left: 100,
      right: 300,
      top: 100,
      bottom: 300,
      width: 200,
      height: 200,
      x: 100,
      y: 100,
      toJSON: () => ({}),
    });
    // A press inside the figure followed by a double-click toggles zoom; it must
    // never dismiss (and the zoom handler must not throw).
    fireEvent.pointerDown(dialog, { clientX: 200, clientY: 200 });
    fireEvent.pointerUp(dialog, { clientX: 200, clientY: 200 });
    fireEvent.dblClick(dialog, { clientX: 200, clientY: 200 });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<ImageLightbox src={SRC} alt="fig" onClose={onClose} />);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("dismisses only on a backdrop click — not on the figure, a pan, or a control", () => {
    const onClose = vi.fn();
    render(<ImageLightbox src={SRC} alt="fig" onClose={onClose} />);

    const dialog = screen.getByRole("dialog");
    const img = screen.getByAltText("fig");
    // Give the figure a known on-screen box so the geometry hit-test is
    // deterministic (jsdom returns an all-zero rect otherwise).
    img.getBoundingClientRect = (): DOMRect => ({
      left: 100,
      right: 300,
      top: 100,
      bottom: 300,
      width: 200,
      height: 200,
      x: 100,
      y: 100,
      toJSON: () => ({}),
    });

    // A press/release INSIDE the figure box must not dismiss (even though the
    // event target may be retargeted off the <img>).
    fireEvent.pointerDown(dialog, { clientX: 200, clientY: 200 });
    fireEvent.pointerUp(dialog, { clientX: 200, clientY: 200 });
    expect(onClose).not.toHaveBeenCalled();

    // A drag that STARTS outside but moves (a pan) must not dismiss.
    fireEvent.pointerDown(dialog, { clientX: 10, clientY: 10 });
    fireEvent.pointerUp(dialog, { clientX: 60, clientY: 60 });
    expect(onClose).not.toHaveBeenCalled();

    // A press on a control must not dismiss (excluded by the controls marker).
    const zoomIn = screen.getByRole("button", { name: /zoom in/i });
    fireEvent.pointerDown(zoomIn, { clientX: 10, clientY: 10 });
    fireEvent.pointerUp(zoomIn, { clientX: 10, clientY: 10 });
    expect(onClose).not.toHaveBeenCalled();

    // A genuine click OUTSIDE the figure box dismisses.
    fireEvent.pointerDown(dialog, { clientX: 10, clientY: 10 });
    fireEvent.pointerUp(dialog, { clientX: 10, clientY: 10 });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("restores body scroll when unmounted", () => {
    const { unmount } = render(
      <ImageLightbox src={SRC} alt="fig" onClose={vi.fn()} />,
    );
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});
