import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { API_BASE_URL } from "@/lib/api";
import { UNSOURCED_RE } from "@/lib/unsourcedMarks";
import type { ChatMessage } from "@/types/domain";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() =>
  server.resetHandlers(
    http.get(`${API_BASE_URL}/feedback/runs/:runId`, () => HttpResponse.json(null)),
    http.get(`${API_BASE_URL}/feedback/sessions/:id`, () => HttpResponse.json([])),
    http.get(`${API_BASE_URL}/feedback/runs`, () => HttpResponse.json([])),
  ),
);

const msg = (content: string): ChatMessage => ({
  role: "assistant",
  content,
  run_id: 9,
  status: "ok",
});

describe("unsourced marks", () => {
  it("parses the reason", () => {
    expect(
      [..."a [unsourced:not_read] b [unsourced: quote_not_found]".matchAll(UNSOURCED_RE)].map(
        (m) => m[1],
      ),
    ).toEqual(["not_read", "quote_not_found"]);
  });

  it("shows a mark that says the source could not be checked, and why", async () => {
    const user = userEvent.setup();
    render(
      <MessageBubble
        message={msg('Tuned by AdamW [unsourced:not_read]; lr 1e-4 [chunk:7].')}
        backendSessionId={1}
      />,
    );
    expect(screen.queryByText(/\[unsourced/)).toBeNull();
    const mark = screen.getByRole("button", {
      name: "Source not verified: the cited passage was not read for this answer",
    });
    expect(screen.getByRole("button", { name: "citation 1" })).toBeInTheDocument();
    await user.click(mark);
    expect(
      await screen.findByText(
        "This claim's citation could not be checked, so it was removed: the cited passage was not read for this answer. Treat the claim as unverified.",
      ),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByText(/could not be checked, so it was removed/)).toBeNull(),
    );
  });

  it("an unknown reason still marks the claim unverified", () => {
    render(<MessageBubble message={msg("X [unsourced:something_new].")} backendSessionId={1} />);
    expect(
      screen.getByRole("button", { name: "Source not verified: the source could not be checked" }),
    ).toBeInTheDocument();
  });
});
