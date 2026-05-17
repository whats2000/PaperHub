import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App shell", () => {
  it("renders the PaperHub heading in the sidebar", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "PaperHub" })).toBeInTheDocument();
  });
});
