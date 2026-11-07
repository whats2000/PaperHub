import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageSquare } from "lucide-react";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("renders title as h1 with meta and actions", () => {
    render(
      <PageHeader
        icon={MessageSquare}
        title="Feedback"
        meta={<span>12 items</span>}
        actions={<button>Export</button>}
      />,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Feedback" }),
    ).toBeInTheDocument();
    expect(screen.getByText("12 items")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
  });

  it("uses the shared header height", () => {
    const { container } = render(
      <PageHeader icon={MessageSquare} title="Ideas" />,
    );
    expect(container.firstChild).toHaveClass("h-(--header-h)");
  });
});
