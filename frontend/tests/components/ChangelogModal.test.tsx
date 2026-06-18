import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChangelogModal } from "@/components/about/ChangelogModal";
import { useVersionStore } from "@/store/version";

afterEach(() => {
  useVersionStore.setState({ info: null, changelogOpen: false });
});

describe("ChangelogModal", () => {
  it("renders entries when open", () => {
    useVersionStore.setState({
      changelogOpen: true,
      info: { current: "2.37.0", latest: null, update_available: false, html_url: null, checked_at: null },
    });
    render(<ChangelogModal />);
    expect(screen.getByRole("heading", { name: /what's new/i })).toBeInTheDocument();
    expect(screen.getByText(/you're on v2\.37\.0/i)).toBeInTheDocument();
  });

  it("shows the update-available row + command when an update exists", () => {
    useVersionStore.setState({
      changelogOpen: true,
      info: {
        current: "2.37.0",
        latest: "2.38.0",
        update_available: true,
        html_url: "https://github.com/whats2000/PaperHub/releases/tag/v2.38.0",
        checked_at: "2026-06-16T00:00:00Z",
      },
    });
    render(<ChangelogModal />);
    expect(screen.getByText(/update available: v2\.38\.0/i)).toBeInTheDocument();
    expect(screen.getByText(/git pull && docker compose up -d --build/i)).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    useVersionStore.setState({ changelogOpen: false });
    const { container } = render(<ChangelogModal />);
    expect(container).toBeEmptyDOMElement();
  });
});
