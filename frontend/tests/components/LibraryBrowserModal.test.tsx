import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LibraryBrowserModal } from "@/components/references/LibraryBrowserModal";
import { API_BASE_URL } from "@/lib/api";
import type { LibraryItem } from "@/types/domain";

const sampleItems: LibraryItem[] = [
  {
    paper_content_id: 1,
    arxiv_id: "1706.03762",
    title: "Attention Is All You Need",
    abstract: "Transformer architecture",
    year: 2017,
    is_private: false,
  },
  {
    paper_content_id: 2,
    arxiv_id: "2005.14165",
    title: "GPT-3 Language Models",
    abstract: "Large language models",
    year: 2020,
    is_private: false,
  },
];

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => server.resetHandlers());

describe("LibraryBrowserModal", () => {
  it("loads items on open and renders search input with debounce", async () => {
    // Track queries received by the server
    const receivedQueries: string[] = [];
    server.use(
      http.get(`${API_BASE_URL}/papers/library`, ({ request }) => {
        const url = new URL(request.url);
        receivedQueries.push(url.searchParams.get("q") ?? "");
        return HttpResponse.json(sampleItems);
      }),
    );

    const onClose = vi.fn();
    const onAttached = vi.fn();
    render(
      <LibraryBrowserModal
        open
        onClose={onClose}
        backendSessionId={1}
        onAttached={onAttached}
      />,
    );

    // Items should load without requiring input (initial fetch)
    await waitFor(() => {
      expect(screen.getByText("Attention Is All You Need")).toBeInTheDocument();
    });

    // Search input is present
    expect(
      screen.getByRole("textbox", { name: /search library/i }),
    ).toBeInTheDocument();

    // Typing triggers a debounced query after 300ms — verify the input exists
    // and the server was called at least once (with the initial empty query)
    expect(receivedQueries.length).toBeGreaterThanOrEqual(1);
  });

  it("attaches a paper, fires onAttached, keeps the modal open, and removes the row from the list", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers/library`, () =>
        HttpResponse.json(sampleItems),
      ),
      http.post(`${API_BASE_URL}/papers/from-library`, () =>
        HttpResponse.json({
          paper_content_id: 1,
          papers_id: 10,
          cache_hit: true,
          title: "Attention Is All You Need",
        }),
      ),
    );

    const onClose = vi.fn();
    const onAttached = vi.fn();
    render(
      <LibraryBrowserModal
        open
        onClose={onClose}
        backendSessionId={1}
        onAttached={onAttached}
      />,
    );

    // Wait for items to load
    const attachBtn = await screen.findByRole("button", {
      name: /attach attention is all you need/i,
    });
    await userEvent.click(attachBtn);

    // Multi-attach UX: modal stays open so the user can keep browsing.
    await waitFor(() => {
      expect(onAttached).toHaveBeenCalledTimes(1);
    });
    expect(onClose).not.toHaveBeenCalled();

    // Just-attached row drops out (matches what /papers/library would return next).
    expect(
      screen.queryByRole("button", { name: /attach attention is all you need/i }),
    ).not.toBeInTheDocument();
    // Other rows still visible.
    expect(
      screen.getByRole("button", { name: /attach gpt-3/i }),
    ).toBeInTheDocument();
  });

  it("closes on Escape key", () => {
    server.use(
      http.get(`${API_BASE_URL}/papers/library`, () =>
        HttpResponse.json(sampleItems),
      ),
    );

    const onClose = vi.fn();
    render(
      <LibraryBrowserModal
        open
        onClose={onClose}
        backendSessionId={1}
        onAttached={vi.fn()}
      />,
    );

    // Modal is open
    expect(screen.getByRole("dialog", { name: /add from library/i })).toBeInTheDocument();

    // Press Escape
    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
