import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/Composer";

describe("Composer", () => {
  it("submits via the send button", async () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);
    await userEvent.type(screen.getByRole("textbox"), "hello world");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onSubmit).toHaveBeenCalledWith("hello world");
  });

  it("submits via Ctrl+Enter", async () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);
    await userEvent.type(
      screen.getByRole("textbox"),
      "hi{Control>}{Enter}{/Control}",
    );
    expect(onSubmit).toHaveBeenCalledWith("hi");
  });

  it("disables the send button when disabled prop is true", () => {
    render(<Composer onSubmit={() => {}} disabled={true} />);
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
  });

  it("does not submit empty / whitespace input", async () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await userEvent.type(screen.getByRole("textbox"), "   ");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("clears the textarea after submit", async () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);
    const textbox = screen.getByRole<HTMLTextAreaElement>("textbox");
    await userEvent.type(textbox, "hello");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(textbox.value).toBe("");
  });
});
