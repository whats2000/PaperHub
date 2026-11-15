import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { OutboundBlockedLine } from "@/components/chat/OutboundBlockedLine";
import { OutboundChip } from "@/components/chat/OutboundChip";

const data = {
  log_id: 4,
  sent_text: "sparsity constraint segmentation small defects",
  original_text: "sparsity-constrained segmentation for KESTREL wafer micro-defects",
  reason: "Removed a product name from your private report.",
  destination: "api.semanticscholar.org",
};

describe("OutboundChip", () => {
  it("shows what was sent and enters with fade + zoom", () => {
    render(<OutboundChip data={data} />);
    expect(screen.getByText("Search sent as")).toBeInTheDocument();
    const chip = screen.getByTestId("outbound-chip").firstElementChild as HTMLElement;
    expect(chip.className).toContain("zoom-in-95");
    expect(chip.className).toContain("origin-top-left");
  });

  it("expands and collapses the detail by height", async () => {
    const user = userEvent.setup();
    render(<OutboundChip data={data} />);
    const btn = screen.getByRole("button", { name: "What changed" });
    const detail = document.getElementById(btn.getAttribute("aria-controls")!)!;
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(detail).toHaveAttribute("data-open", "false");
    expect(detail.className).toContain("grid-rows-[0fr]");
    expect(detail.className).toContain("transition-[grid-template-rows]");

    await user.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(detail).toHaveAttribute("data-open", "true");
    // Sent / Written (struck) / Why / To (localized destination).
    expect(screen.getByText("Written")).toBeInTheDocument();
    expect(screen.getByText(data.original_text).tagName).toBe("DEL");
    expect(screen.getByText(data.reason)).toBeInTheDocument();
    expect(screen.getByText("Semantic Scholar")).toBeInTheDocument();

    await user.click(btn);
    expect(detail).toHaveAttribute("data-open", "false");
  });

  it("labels the public web search destination", async () => {
    const user = userEvent.setup();
    render(<OutboundChip data={{ ...data, destination: "public-web-search" }} />);
    await user.click(screen.getByRole("button", { name: "What changed" }));
    expect(screen.getByText("public web search")).toBeInTheDocument();
  });
});

describe("OutboundBlockedLine", () => {
  it("names the sensitive term", () => {
    render(
      <OutboundBlockedLine
        data={{ log_id: 9, text: "KESTREL v3 defect dataset", reason: "term", term: "KESTREL" }}
      />,
    );
    expect(screen.getByTestId("outbound-blocked").textContent).toBe(
      "Not sent: KESTREL v3 defect dataset contains a sensitive term (KESTREL).",
    );
  });

  it("falls back to the reason without a term", () => {
    render(
      <OutboundBlockedLine
        data={{ log_id: 9, text: "my query", reason: "It names a private experiment." }}
      />,
    );
    expect(screen.getByTestId("outbound-blocked").textContent).toBe(
      "Not sent: my query. It names a private experiment.",
    );
  });
});
