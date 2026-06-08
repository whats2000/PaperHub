import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";

describe("Composer slideChip prop", () => {
  it("renders the slide chip when slideChip prop is provided", () => {
    const onToggle = vi.fn();
    render(
      <Composer onSubmit={() => {}} disabled={false}
        slideChip={{ page: 5, attached: true, onToggle }} />,
    );
    expect(screen.getByText(/Slide 5/)).toBeInTheDocument();
  });

  it("omits the slide chip when slideChip is null", () => {
    render(<Composer onSubmit={() => {}} disabled={false} slideChip={null} />);
    expect(screen.queryByText(/Slide/)).not.toBeInTheDocument();
  });
});

describe("Composer i18n", () => {
  it("localizes the send label when the language switches", async () => {
    const { default: i18n } = await import("../../lib/i18n");
    await i18n.changeLanguage("ja");
    render(<Composer onSubmit={() => {}} disabled={false} />);
    expect(screen.getByLabelText("送信")).toBeInTheDocument();
    await i18n.changeLanguage("en");
  });
});
