import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The reader itself is covered in CitationCanvas tests; here only its frame.
vi.mock("@/components/canvas/CitationCanvas", () => ({
  CitationCanvas: ({ standalone }: { standalone: { papers: { title: string }[]; onClose: () => void } }) => (
    <div data-testid="reader">
      {standalone.papers[0]?.title}
      <button type="button" onClick={standalone.onClose}>
        Close reader
      </button>
    </div>
  ),
}));

import { PaperReaderDrawer } from "./PaperReaderDrawer";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const paper = { paper_content_id: 9, title: "Ghost imaging" };

describe("PaperReaderDrawer", () => {
  it("slides in with the paper and slides out on close before unmounting", () => {
    const onClose = vi.fn();
    const { rerender } = render(<PaperReaderDrawer paper={paper} onClose={onClose} />);
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

  it("closes on Escape and keeps the key from also closing the drawer beneath", () => {
    const onClose = vi.fn();
    render(<PaperReaderDrawer paper={paper} onClose={onClose} />);
    const beneath = vi.fn((e: KeyboardEvent) => e.defaultPrevented);
    document.addEventListener("keydown", beneath);
    fireEvent.keyDown(document, { key: "Escape" });
    document.removeEventListener("keydown", beneath);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(beneath).toHaveReturnedWith(true);
  });

  it("leaves Escape to a field the reader is typing in", () => {
    const onClose = vi.fn();
    render(
      <>
        <input aria-label="field" />
        <PaperReaderDrawer paper={paper} onClose={onClose} />
      </>,
    );
    fireEvent.keyDown(screen.getByLabelText("field"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});
