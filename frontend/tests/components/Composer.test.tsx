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

  it("submits via plain Enter (no modifier)", async () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);
    await userEvent.type(screen.getByRole("textbox"), "hi");
    await userEvent.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("hi");
  });

  it("Shift+Enter inserts newline, does NOT submit", async () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);
    const textbox = screen.getByRole<HTMLTextAreaElement>("textbox");
    await userEvent.type(textbox, "line one");
    await userEvent.keyboard("{Shift>}{Enter}{/Shift}");
    await userEvent.type(textbox, "line two");
    // Should NOT have submitted yet
    expect(onSubmit).not.toHaveBeenCalled();
    // Submit via button
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]![0]).toContain("line one");
    expect(onSubmit.mock.calls[0]![0]).toContain("line two");
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

  it("renders the AttachPaperMenu trigger, an enabled References toggle, and 2 disabled capability buttons", () => {
    render(<Composer onSubmit={() => {}} disabled={false} />);
    // The paperclip is now an enabled AttachPaperMenu popover trigger.
    const attach = screen.getByRole("button", { name: /attach paper/i });
    expect(attach).not.toBeDisabled();
    // References is now wired to the Citation Canvas toggle (Plan D Wave 2).
    expect(screen.getByRole("button", { name: /^references$/i })).toBeEnabled();
    // The remaining placeholder capabilities stay disabled.
    const labels = ["Slides", "Compare"];
    for (const label of labels) {
      const btn = screen.getByRole("button", { name: label });
      expect(btn).toBeDisabled();
    }
  });
});
