import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { IdeationFeedback } from "@/components/feedback/IdeationFeedback";
import { API_BASE_URL } from "@/lib/api";
import type { IdeationFeedbackSummary } from "@/types/ideation";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let summary: IdeationFeedbackSummary = {
  save_rate: 0.4,
  worked_rate: 0.25,
  dismiss_reasons: [
    { reason: "already_tried", count: 3 },
    { reason: "not_feasible", count: 1 },
  ],
};
let posted = 0;

const server = setupServer(
  http.get(`${API_BASE_URL}/ideation/feedback-summary`, () => HttpResponse.json(summary)),
  http.post(`${API_BASE_URL}/ideation/feedback-to-g1`, () => {
    posted += 1;
    return HttpResponse.json({ appended: 3 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  posted = 0;
  vi.clearAllMocks();
});
afterAll(() => server.close());

describe("IdeationFeedback", () => {
  it("shows the rates and the dismissal reasons with counts", async () => {
    render(<IdeationFeedback />);
    expect(await screen.findByText("40%")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.getByText("Already tried")).toBeInTheDocument();
    expect(screen.getByText("Not feasible")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("How good are the ideas?")).toBeInTheDocument();
  });

  it("shows an em dash when there are no outcomes", async () => {
    summary = { ...summary, worked_rate: null };
    render(<IdeationFeedback />);
    await screen.findByText("40%");
    expect(screen.getByTestId("worked-rate")).toHaveTextContent("—");
    summary = { ...summary, worked_rate: 0.25 };
  });

  it("posts feedback-to-g1 and toasts the appended count", async () => {
    render(<IdeationFeedback />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Write to ideation eval data" }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(posted).toBe(1);
    expect(vi.mocked(toast.success).mock.calls[0]?.[0]).toContain("3");
  });

  it("Retry goes back to the loading state, then shows the summary", async () => {
    let fail = true;
    let release: () => void = () => {};
    server.use(
      http.get(`${API_BASE_URL}/ideation/feedback-summary`, async () => {
        if (fail) return new HttpResponse(null, { status: 500 });
        await new Promise<void>((r) => {
          release = r;
        });
        return HttpResponse.json(summary);
      }),
    );
    render(<IdeationFeedback />);
    expect(await screen.findByText("Couldn't load ideation feedback.")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load ideation feedback.")).not.toBeInTheDocument();
    release();
    expect(await screen.findByText("40%")).toBeInTheDocument();
  });
});
