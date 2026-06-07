import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";

// Mock clipboard API — keep a reference to the spy outside navigator so lint
// doesn't flag navigator.clipboard.writeText as an unbound method access.
const writeTextMock = vi.fn().mockResolvedValue(undefined);
beforeEach(() => {
  writeTextMock.mockClear();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: writeTextMock },
    configurable: true,
  });
});

describe("MessageBubble", () => {
  it("renders a user message right-aligned", () => {
    render(
      <MessageBubble message={{ role: "user", content: "hello", run_id: null }} />,
    );
    const node = screen.getByText("hello");
    expect(node.closest("article")).toHaveAttribute("data-role", "user");
  });

  it("renders streaming state for an in-flight assistant message", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "Hi th", run_id: 1, status: "streaming",
        }}
      />,
    );
    expect(screen.getByText(/hi th/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/streaming/i)).toBeInTheDocument();
  });

  it("renders an error message with the error string", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "Provider 500",
        }}
      />,
    );
    expect(screen.getByText(/provider 500/i)).toBeInTheDocument();
  });

  it("shows Retry button on error message when onRetry is provided", async () => {
    const onRetry = vi.fn();
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "Something failed",
        }}
        onRetry={onRetry}
      />,
    );
    const retryBtn = screen.getByRole("button", { name: /retry/i });
    expect(retryBtn).toBeInTheDocument();
    await userEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not show Retry button on error message when onRetry is absent", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "Something failed",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("renders Copy button on completed assistant messages", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "Here is the answer", run_id: 1, status: "ok",
        }}
      />,
    );
    expect(screen.getByRole("button", { name: /copy message/i })).toBeInTheDocument();
  });

  it("copy button calls clipboard.writeText with message content", async () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "Clipboard text", run_id: 1, status: "ok",
        }}
      />,
    );
    const copyBtn = screen.getByRole("button", { name: /copy message/i });
    await userEvent.click(copyBtn);
    expect(writeTextMock).toHaveBeenCalledWith("Clipboard text");
  });

  it("does not show Copy button on streaming messages", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "partial", run_id: 1, status: "streaming",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /copy message/i })).toBeNull();
  });

  it("does not show Copy button on error messages", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "oops",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /copy message/i })).toBeNull();
  });

  it("renders user content as plain text (no HTML execution)", () => {
    render(
      <MessageBubble
        message={{
          role: "user",
          content: "<img src=x onerror=alert(1)>",
          run_id: null,
        }}
      />,
    );
    // The literal angle brackets must be present in textContent — no <img> element.
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    const article = screen.getByText(/<img/).closest("article");
    expect(article?.querySelector("img")).toBeNull();
  });

  it("renders assistant raw HTML as escaped text (no script execution)", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "Result: <img src=x onerror=alert(1)>",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    const article = screen.getByText(/result/i).closest("article");
    // No <img> element should exist — react-markdown renders it as text.
    expect(article?.querySelector("img")).toBeNull();
    // The literal characters should appear (react-markdown shows them as text).
    expect(article?.textContent).toContain("<img");
  });

  it("renders inline LaTeX math as KaTeX (not raw $...$)", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "The loss is $E = mc^2$ in this model.",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    // rehype-katex emits .katex spans; the raw dollar-delimited source
    // must NOT survive into the rendered text.
    expect(container.querySelector(".katex")).not.toBeNull();
    const article = container.querySelector("article");
    expect(article?.textContent).not.toContain("$E = mc^2$");
  });

  it("renders \\( ... \\) inline math (issue #5) as KaTeX, not plain text", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          // The reporter's exact wording from issue #5.
          content: "The result is \\(E = mc^2\\).",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    // The TeX-style \( ... \) delimiters must be normalized + rendered by KaTeX.
    // (KaTeX keeps the LaTeX source in a MathML annotation, so we assert on the
    // delimiters not surviving — same convention as the $$ / $ tests above.)
    expect(container.querySelector(".katex")).not.toBeNull();
    // Inline math, not display: a \( ... \) pair must NOT promote to .katex-display.
    expect(container.querySelector(".katex-display")).toBeNull();
    const article = container.querySelector("article");
    expect(article?.textContent).not.toContain("\\(");
    expect(article?.textContent).not.toContain("\\)");
  });

  it("renders \\[ ... \\] display math (issue #5) as KaTeX, not plain text", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "The objective:\n\n\\[ \\sum_i \\alpha_i x_i \\]\n\nDone.",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    expect(container.querySelector(".katex-display")).not.toBeNull();
    const article = container.querySelector("article");
    expect(article?.textContent).not.toContain("\\[");
    expect(article?.textContent).not.toContain("\\]");
  });

  it("renders a bare \\begin{equation} environment as KaTeX", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "The loss:\n\n\\begin{equation}\nE = mc^2\n\\end{equation}\n\nDone.",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    // remark-math only sees math after normalizeMath wraps the env in $$;
    // a successful KaTeX render (display mode) proves the pipeline consumed it
    // rather than passing it through as raw text.
    expect(container.querySelector(".katex-display")).not.toBeNull();
    const article = container.querySelector("article");
    // The injected $$ fences must not survive (KaTeX keeps the LaTeX source in
    // a MathML annotation, so we check the delimiters, not the env name).
    expect(article?.textContent).not.toContain("$$");
  });

  it("renders a chunk citation emitted inside an equation as a clickable marker", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "\\begin{equation}\nE = mc^2 [chunk:78081]\n\\end{equation}",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    // The equation still renders as KaTeX (the marker didn't pollute the math).
    expect(container.querySelector(".katex-display")).not.toBeNull();
    // The lifted citation became a clickable superscript marker, not raw text.
    expect(screen.getByRole("button", { name: /citation/i })).toBeInTheDocument();
    expect(container.querySelector("article")?.textContent).not.toContain("[chunk:78081]");
  });

  it("renders \\mathbbm (bbm package) via the KaTeX macro mapping", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "The indicator $\\mathbbm{1}[x>0]$ and the reals $\\mathbbm{R}$.",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    // A successful render proves \mathbbm expanded; an unmapped command would
    // surface as a rehype-katex error node, not a .katex span.
    expect(container.querySelector(".katex")).not.toBeNull();
    const article = container.querySelector("article");
    // The raw command must not leak as visible text, and KaTeX must not have
    // emitted its red parse-error markup.
    expect(article?.querySelector(".katex-error")).toBeNull();
    expect(article?.textContent).not.toContain("$\\mathbbm");
  });

  it("renders \\Tilde (capital wide-tilde idiom) via the KaTeX macro mapping", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "The reverse process uses $\\Tilde{R}_t$ as the estimate.",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    // \Tilde isn't a KaTeX builtin; the macro maps it to \widetilde so it
    // renders instead of surfacing a red parse-error node.
    expect(container.querySelector(".katex")).not.toBeNull();
    const article = container.querySelector("article");
    // No red parse-error node, and the raw $-delimited source didn't survive as
    // text (KaTeX keeps \Tilde{R} only in its MathML annotation, like \mathbbm).
    expect(article?.querySelector(".katex-error")).toBeNull();
    expect(article?.textContent).not.toContain("$\\Tilde");
  });

  it("renders block LaTeX math ($$...$$) as KaTeX", () => {
    const { container } = render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "Objective:\n\n$$\\sum_{i=1}^{n} \\alpha_i x_i$$\n\nDone.",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    // KaTeX rendered the block (rehype-katex emits .katex spans).
    expect(container.querySelector(".katex")).not.toBeNull();
    const article = container.querySelector("article");
    // The raw $$ delimiters must not survive (KaTeX keeps the LaTeX source
    // in a MathML annotation, so we check the delimiters, not \\sum).
    expect(article?.textContent).not.toContain("$$");
  });
});
