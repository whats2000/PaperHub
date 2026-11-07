import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/layout/Sidebar";
import { useFeedbackBadge } from "@/hooks/useFeedbackBadge";
import { API_BASE_URL } from "@/lib/api";
import { FeedbackPage } from "@/pages/FeedbackPage";
import { useNavStore } from "@/store/nav";
import type { FeedbackSummary, QueueItem, QueueSubmit } from "@/types/feedback";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const item = (id: number): QueueItem => ({
  feedback_id: id,
  run_id: 100 + id,
  session_id: 1,
  session_title: `Session ${id}`,
  papers: ["Paper A"],
  earlier: [],
  question: `Question ${id}?`,
  answer: `Answer ${id}.`,
  flagged: `no, I meant ${id}`,
  next_reply: null,
  reviewer_doubt: `doubt ${id}`,
  guessed_reasons: ["off_topic"],
});

const server = setupServer();
let queue: QueueItem[] = [];
let submitted: QueueSubmit | null = null;
let confirmedTotal = 0;
let summaryCalls = 0;

const summary = (): FeedbackSummary => ({
  kpis: {
    // Starts at 40 answers; each confirmed flag adds one negative answer.
    answers_with_feedback: 40 + confirmedTotal,
    approval_rate: null,
    negatives_explicit: 0,
    negatives_implicit: confirmedTotal,
    pending: queue.length,
  },
  stages: [],
  other_reasons: [],
});

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  queue = [item(1), item(2), item(3)];
  submitted = null;
  confirmedTotal = 0;
  summaryCalls = 0;
  useNavStore.setState({ page: "chat", feedbackBadge: 0 });
  server.use(
    http.get(`${API_BASE_URL}/ideation/feedback-summary`, () =>
      HttpResponse.json({ save_rate: 0, worked_rate: null, dismiss_reasons: [] }),
    ),
    http.get(`${API_BASE_URL}/feedback/queue`, () => HttpResponse.json(queue)),
    http.post(`${API_BASE_URL}/feedback/queue/submit`, async ({ request }) => {
      submitted = (await request.json()) as QueueSubmit;
      const ids = new Set(submitted.answers.map((a) => a.feedback_id));
      queue = queue.filter((q) => !ids.has(q.feedback_id));
      const confirmed = submitted.answers.filter((a) => a.confirmed).length;
      confirmedTotal += confirmed;
      return HttpResponse.json({ confirmed, rejected: submitted.answers.length - confirmed });
    }),
    http.get(`${API_BASE_URL}/feedback/summary`, () => {
      summaryCalls += 1;
      return HttpResponse.json(summary());
    }),
    http.get(`${API_BASE_URL}/feedback/presets`, () => HttpResponse.json([])),
  );
});
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

/** The app's slice that this flow crosses: the badge hook, the sidebar, the page. */
function Harness() {
  useFeedbackBadge();
  const page = useNavStore((s) => s.page);
  return (
    <div>
      <Sidebar />
      {page === "feedback" && <FeedbackPage />}
    </div>
  );
}

const showsItem = (id: number) =>
  waitFor(() => expect(screen.getByText(`Question ${id}?`)).toBeInTheDocument());

describe("Feedback flywheel, end to end", () => {
  it("badge → page → confirm two items → submit → summary refetched", async () => {
    render(<Harness />);

    // 1. The badge counts the queue before the page is ever opened.
    expect(await screen.findByText("3 need your confirmation")).toBeInTheDocument();
    expect(useNavStore.getState().feedbackBadge).toBe(3);
    expect(summaryCalls).toBe(0);

    // 2. The sidebar entry opens the Feedback page: banner + analysis.
    await userEvent.click(screen.getByRole("button", { name: /^Feedback/ }));
    expect(await screen.findByText("3 items need your confirmation")).toBeInTheDocument();
    expect(await screen.findByText("40")).toBeInTheDocument();
    expect(summaryCalls).toBe(1);

    // 3. Confirm the first item, reject the second.
    await userEvent.click(screen.getByRole("button", { name: "Start confirming" }));
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: /Yes, the answer was wrong/ }));
    await showsItem(2);
    await userEvent.click(screen.getByRole("button", { name: /No, I was adding/ }));

    // 4. Submit the two answers; the third stays queued.
    await userEvent.click(screen.getByRole("button", { name: "Submit 2" }));
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted!.answers).toEqual([
      { feedback_id: 1, confirmed: true, reasons: ["off_topic"] },
      { feedback_id: 2, confirmed: false, reasons: [] },
    ]);

    // 5. The badge follows the queue and the analysis is refetched.
    await waitFor(() => expect(useNavStore.getState().feedbackBadge).toBe(1));
    expect(screen.getByText("1 need your confirmation")).toBeInTheDocument();
    await waitFor(() => expect(summaryCalls).toBe(2));
    expect(await screen.findByText("41")).toBeInTheDocument();
    await showsItem(3);
    // Item 3 is already on screen (answering item 2 auto-advanced to it); the
    // banner count drops only when the post-submit slide swaps the items in,
    // one SLIDE_OUT_MS later — wait for it rather than racing it.
    expect(await screen.findByText("1 item needs your confirmation")).toBeInTheDocument();
  });
});
