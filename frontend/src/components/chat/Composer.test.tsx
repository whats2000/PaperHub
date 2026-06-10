import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

describe("Composer setup gate", () => {
  it("shows the setup hint and opens settings when setupRequired", async () => {
    const onOpenSettings = vi.fn();
    render(
      <Composer
        onSubmit={() => {}}
        disabled={true}
        setupRequired={true}
        onOpenSettings={onOpenSettings}
      />,
    );
    expect(screen.getByText(/Finish setup/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/Open Settings/i));
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });

  it("omits the setup hint when not required", () => {
    render(<Composer onSubmit={() => {}} disabled={false} />);
    expect(screen.queryByText(/Finish setup/i)).not.toBeInTheDocument();
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
