import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PresenterControls } from "@/components/slides/PresenterControls";

describe("PresenterControls", () => {
  const base = {
    startedAt: 1000,
    currentPage: 2,
    numPages: 10,
    audienceConnected: true,
    onStop: () => {},
    now: () => 1000 + 65_000, // 65s elapsed
  };

  it("renders elapsed time mm:ss from startedAt", () => {
    render(<PresenterControls {...base} />);
    expect(screen.getByLabelText("elapsed time").textContent).toBe("01:05");
  });

  it("shows audience-connected state", () => {
    render(<PresenterControls {...base} />);
    expect(screen.getByText(/audience connected/i)).toBeInTheDocument();
  });

  it("shows audience-closed state when disconnected", () => {
    render(<PresenterControls {...base} audienceConnected={false} />);
    expect(screen.getByText(/audience window closed/i)).toBeInTheDocument();
  });

  it("calls onStop when Stop is clicked", () => {
    const onStop = vi.fn();
    render(<PresenterControls {...base} onStop={onStop} />);
    fireEvent.click(screen.getByLabelText("stop presenting"));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("renders the nextPreview slot when a next slide exists", () => {
    render(
      <PresenterControls
        {...base}
        nextPreview={<div data-testid="next" />}
      />,
    );
    expect(screen.getByTestId("next")).toBeInTheDocument();
  });

  it("hides the preview on the last slide (and still shows Stop)", () => {
    render(
      <PresenterControls
        {...base}
        currentPage={10}
        numPages={10}
        nextPreview={<div data-testid="next" />}
      />,
    );
    expect(screen.queryByTestId("next")).not.toBeInTheDocument();
    expect(screen.queryByText(/next →/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("stop presenting")).toBeInTheDocument();
  });
});
