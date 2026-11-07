import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import i18n from "@/lib/i18n";
import { PaperLoading } from "@/components/canvas/PaperLoading";

describe("canvas i18n", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("localizes the loading label when the language switches", async () => {
    // PaperLoading's default label reads the `canvas` namespace and needs no
    // store / network — the cleanest probe for the canvas catalog.
    render(<PaperLoading />);
    expect(screen.getByText("Loading paper…")).toBeInTheDocument();

    await i18n.changeLanguage("ja");
    expect(screen.getByText("論文を読み込み中…")).toBeInTheDocument();
  });
});

describe("PaperLoading skeleton", () => {
  it("is a page-shaped shimmer skeleton with a status label and no spinner", () => {
    const { container } = render(<PaperLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading paper…");
    const bars = container.querySelectorAll(".skeleton-shimmer");
    expect(bars.length).toBeGreaterThan(8);
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });
});
