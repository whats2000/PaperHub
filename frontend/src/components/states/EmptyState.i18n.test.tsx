import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import i18n from "../../lib/i18n";
import { EmptyState } from "./EmptyState";

describe("EmptyState i18n", () => {
  it("localizes the welcome heading when the language switches", async () => {
    // EmptyState reads only the chat store (no provider/network), so it
    // exercises the states namespace's empty.* chrome in isolation.
    render(<EmptyState />);
    expect(
      screen.getByText("What can I help you with?"),
    ).toBeInTheDocument();

    await i18n.changeLanguage("ja");
    expect(
      screen.getByText("何かお手伝いできることはありますか？"),
    ).toBeInTheDocument();

    await i18n.changeLanguage("en");
  });
});
