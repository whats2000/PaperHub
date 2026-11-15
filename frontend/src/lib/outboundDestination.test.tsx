import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { resetDestinationCache, useDestinationLabel } from "@/lib/outboundDestination";

const server = setupServer(
  http.get(`${API_BASE_URL}/outbound/destinations`, () =>
    HttpResponse.json([
      { host: "search.lab.internal", source: "trusted_search" },
      { host: "arxiv.org", source: "paper_source" },
    ]),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => resetDestinationCache());

function Label({ d }: { d: string }) {
  return <span data-testid="l">{useDestinationLabel(d)}</span>;
}

describe("useDestinationLabel", () => {
  it("labels the trusted search host", async () => {
    render(<Label d="search.lab.internal" />);
    expect(await screen.findByText("trusted search")).toBeInTheDocument();
  });

  it("shows any other host as is", async () => {
    render(<Label d="arxiv.org" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByTestId("l").textContent).toBe("arxiv.org");
  });
});
