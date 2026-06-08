import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import i18n from "../../lib/i18n";
import { AccountMenu } from "./AccountMenu";

describe("AccountMenu", () => {
  it("opens and switches the UI language", async () => {
    const user = userEvent.setup();
    render(<AccountMenu collapsed={false} onOpenSettings={() => {}} />);
    await user.click(screen.getByRole("button", { name: /account/i }));
    // The Language label is visible (English source catalog). Base UI renders
    // the popup (which contains the radio groups) on a later tick, so query async.
    expect(await screen.findByText("Language")).toBeInTheDocument();
    await user.click(await screen.findByRole("menuitemradio", { name: "日本語" }));
    expect(i18n.language).toBe("ja");
    await i18n.changeLanguage("en");
  });

  it("invokes onOpenSettings when Settings is clicked", async () => {
    const user = userEvent.setup();
    let opened = false;
    render(<AccountMenu collapsed={false} onOpenSettings={() => (opened = true)} />);
    await user.click(screen.getByRole("button", { name: /account/i }));
    await user.click(await screen.findByRole("menuitem", { name: "Settings" }));
    expect(opened).toBe(true);
  });
});
