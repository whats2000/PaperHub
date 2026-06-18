import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountMenu } from "@/components/layout/AccountMenu";
import { useVersionStore } from "@/store/version";

// Mock next-themes so ThemeProvider / useTheme don't need a DOM provider.
vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));

afterEach(() => {
  // Reset the version store between tests.
  useVersionStore.setState({ info: null, changelogOpen: false });
});

describe("AccountMenu — About item opens changelog", () => {
  it("clicking About sets changelogOpen=true in the version store", async () => {
    const user = userEvent.setup();

    render(<AccountMenu collapsed={false} onOpenSettings={vi.fn()} />);

    // Open the menu via its trigger button.
    await user.click(screen.getByRole("button", { name: /account/i }));

    // The About item must be present and clickable.
    const aboutItem = await screen.findByText(/about/i);
    await user.click(aboutItem);

    expect(useVersionStore.getState().changelogOpen).toBe(true);
  });

  it("shows an amber update badge on the avatar when update_available", () => {
    useVersionStore.setState({
      info: {
        current: "2.37.0",
        latest: "2.38.0",
        update_available: true,
        html_url: null,
        checked_at: null,
      },
      changelogOpen: false,
    });

    render(<AccountMenu collapsed={false} onOpenSettings={vi.fn()} />);

    // The dot is rendered as a span with aria-label from the updateBadge key.
    expect(
      screen.getByLabelText(/update available/i),
    ).toBeInTheDocument();
  });

  it("does NOT show the update badge when update_available is false", () => {
    useVersionStore.setState({
      info: {
        current: "2.37.0",
        latest: null,
        update_available: false,
        html_url: null,
        checked_at: null,
      },
      changelogOpen: false,
    });

    render(<AccountMenu collapsed={false} onOpenSettings={vi.fn()} />);

    expect(
      screen.queryByLabelText(/update available/i),
    ).toBeNull();
  });
});
