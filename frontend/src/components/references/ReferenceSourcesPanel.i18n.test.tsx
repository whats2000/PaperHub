import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import i18n from "../../lib/i18n";
import { ReferenceSourcesPanel } from "./ReferenceSourcesPanel";

describe("ReferenceSourcesPanel i18n", () => {
  it("localizes the no-session prompt when the language switches", async () => {
    // No active session → renders the noSession chrome string, which needs no
    // store / network and exercises the references namespace.
    render(<ReferenceSourcesPanel frontendSessionId={null} />);
    expect(
      screen.getByText("Start or pick a chat to manage its references."),
    ).toBeInTheDocument();

    await i18n.changeLanguage("ja");
    expect(
      screen.getByText(
        "チャットを開始または選択して、その参考文献を管理してください。",
      ),
    ).toBeInTheDocument();

    await i18n.changeLanguage("en");
  });
});
