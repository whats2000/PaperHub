import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/Composer";

describe("Composer Stop button", () => {
  it("shows Stop button (not Send) while isStreaming=true", () => {
    render(
      <Composer
        onSubmit={() => {}}
        disabled={false}
        isStreaming={true}
        onStop={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^send$/i })).not.toBeInTheDocument();
  });

  it("calls onStop when the Stop button is clicked", async () => {
    const onStop = vi.fn();
    render(
      <Composer
        onSubmit={() => {}}
        disabled={false}
        isStreaming={true}
        onStop={onStop}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("Stop button is not disabled while isStreaming", () => {
    render(
      <Composer
        onSubmit={() => {}}
        disabled={true}
        isStreaming={true}
        onStop={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /stop/i })).not.toBeDisabled();
  });

  it("shows Send (not Stop) when isStreaming is falsy (idle state)", () => {
    render(<Composer onSubmit={() => {}} disabled={false} />);
    expect(screen.getByRole("button", { name: /^send$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
  });

  it("shows Send when isStreaming=false explicitly", () => {
    render(
      <Composer
        onSubmit={() => {}}
        disabled={false}
        isStreaming={false}
      />,
    );
    expect(screen.getByRole("button", { name: /^send$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
  });
});
