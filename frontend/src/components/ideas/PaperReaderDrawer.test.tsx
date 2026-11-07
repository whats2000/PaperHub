import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The reader itself is covered in CitationCanvas tests; here only its frame.
vi.mock("@/components/canvas/CitationCanvas", async () => {
  const { createPortal } = await import("react-dom");
  return {
    CitationCanvas: ({ standalone }: { standalone: { papers: { title: string }[]; onClose: () => void } }) => (
      <div data-testid="reader">
        {standalone.papers[0]?.title}
        <button type="button" onClick={standalone.onClose}>
          Close reader
        </button>
        {/* Like the translation card or the language menu: portalled out of the reader. */}
        {createPortal(<button type="button">Portalled menu item</button>, document.body)}
      </div>
    ),
  };
});

import { PaperReaderDrawer } from "./PaperReaderDrawer";

beforeEach(() => {
  vi.useFakeTimers();
  try {
    localStorage.clear();
  } catch {
    /* no storage */
  }
});
afterEach(() => vi.useRealTimers());

const paper = { paper_content_id: 9, title: "Ghost imaging" };

/** Let the lazily imported reader load. */
const settle = () =>
  act(async () => {
    await vi.dynamicImportSettled();
  });

describe("PaperReaderDrawer", () => {
  it("slides in with the paper and slides out on close before unmounting", async () => {
    const onClose = vi.fn();
    const { rerender } = render(<PaperReaderDrawer paper={paper} onClose={onClose} />);
    await settle();
    expect(screen.getByTestId("reader")).toHaveTextContent("Ghost imaging");
    act(() => {
      vi.advanceTimersByTime(50);
    }); // the next frame
    expect(screen.getByTestId("paper-reader-drawer")).toHaveClass("translate-x-0");

    rerender(<PaperReaderDrawer paper={null} onClose={onClose} />);
    const drawer = screen.getByTestId("paper-reader-drawer");
    expect(drawer).toHaveClass("translate-x-full"); // still mounted, sliding out
    expect(screen.getByTestId("reader")).toHaveTextContent("Ghost imaging");
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.queryByTestId("paper-reader-drawer")).not.toBeInTheDocument();
  });

  it("closes on Escape and keeps the key from also closing the drawer beneath", async () => {
    const onClose = vi.fn();
    render(<PaperReaderDrawer paper={paper} onClose={onClose} />);
    const beneath = vi.fn((e: KeyboardEvent) => e.defaultPrevented);
    await settle();
    document.addEventListener("keydown", beneath);
    fireEvent.keyDown(document, { key: "Escape" });
    document.removeEventListener("keydown", beneath);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(beneath).toHaveReturnedWith(true);
  });

  it("leaves Escape to a field the reader is typing in", async () => {
    const onClose = vi.fn();
    render(
      <>
        <input aria-label="field" />
        <PaperReaderDrawer paper={paper} onClose={onClose} />
      </>,
    );
    await settle();
    fireEvent.keyDown(screen.getByLabelText("field"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("widens when its left edge is dragged left, and remembers the width", async () => {
    window.innerWidth = 1600; // the 90%-of-window cap stays out of the way
    const { unmount } = render(<PaperReaderDrawer paper={paper} onClose={vi.fn()} />);
    const drawer = screen.getByTestId("paper-reader-drawer");
    await settle();
    expect(drawer.style.getPropertyValue("--reader-w")).toBe("820px");
    const edge = screen.getByRole("separator");
    fireEvent.pointerDown(edge, { clientX: 1000 });
    fireEvent.pointerMove(window, { clientX: 880 });
    fireEvent.pointerUp(window);
    expect(drawer.style.getPropertyValue("--reader-w")).toBe("940px");
    unmount();
    render(<PaperReaderDrawer paper={paper} onClose={vi.fn()} />);
    expect(screen.getByTestId("paper-reader-drawer").style.getPropertyValue("--reader-w")).toBe("940px");
    await settle();
  });

  it("a click outside closes only the reader and does not reach what is underneath", async () => {
    const onClose = vi.fn();
    const underneath = vi.fn();
    render(
      <>
        <button type="button" onClick={underneath}>
          Graph
        </button>
        <PaperReaderDrawer paper={paper} onClose={onClose} />
      </>,
    );
    await settle();
    const graph = screen.getByRole("button", { name: "Graph" });
    fireEvent.pointerDown(graph);
    fireEvent.click(graph);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(underneath).not.toHaveBeenCalled();
  });

  it("a click inside the reader, or in a popup it portalled out, keeps it open", async () => {
    const onClose = vi.fn();
    render(<PaperReaderDrawer paper={paper} onClose={onClose} />);
    fireEvent.pointerDown(screen.getByTestId("reader"));
    await settle();
    fireEvent.pointerDown(screen.getByRole("button", { name: "Portalled menu item" }));
    expect(onClose).not.toHaveBeenCalled();
  });
});
