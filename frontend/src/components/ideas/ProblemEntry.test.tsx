import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useIdeationStore } from "@/store/ideation";

import { ProblemEntry, PROBLEM_MAX_CHARS } from "./ProblemEntry";

describe("ProblemEntry", () => {
  const submitProblem = vi.fn();
  beforeEach(() => {
    submitProblem.mockReset().mockResolvedValue(undefined);
    useIdeationStore.setState({ submitProblem, submitting: false });
  });

  it("is a multi-line entry with a length limit", () => {
    render(<ProblemEntry inputRef={createRef<HTMLTextAreaElement>()} />);
    const box = screen.getByRole("textbox", { name: "Problem" });
    expect(box.tagName).toBe("TEXTAREA");
    expect(box).toHaveAttribute("maxlength", String(PROBLEM_MAX_CHARS));
  });

  it("submits on Enter and keeps Shift+Enter as a newline", async () => {
    const user = userEvent.setup();
    render(<ProblemEntry inputRef={createRef<HTMLTextAreaElement>()} />);
    const box = screen.getByRole("textbox", { name: "Problem" });
    await user.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(submitProblem).not.toHaveBeenCalled();
    expect(box).toHaveValue("line one\nline two");
    await user.type(box, "{Enter}");
    expect(submitProblem).toHaveBeenCalledWith("line one\nline two", "");
  });

  it("shows a counter only near the limit", async () => {
    const user = userEvent.setup();
    render(<ProblemEntry inputRef={createRef<HTMLTextAreaElement>()} />);
    const box = screen.getByRole("textbox", { name: "Problem" });
    await user.type(box, "short");
    expect(screen.queryByTestId("entry-counter")).toBeNull();
    await user.click(box);
    await user.paste("x".repeat(PROBLEM_MAX_CHARS * 0.8));
    expect(screen.getByTestId("entry-counter")).toHaveTextContent(`/${PROBLEM_MAX_CHARS}`);
  });
});
