import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { FeedbackPage } from "@/pages/FeedbackPage";
import { useNavStore } from "@/store/nav";
import type { QueueItem, QueueSubmit } from "@/types/feedback";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const item = (id: number, extra: Partial<QueueItem> = {}): QueueItem => ({
  feedback_id: id,
  run_id: 100 + id,
  session_id: 1,
  session_title: `Session ${id}`,
  papers: ["Paper A"],
  earlier: [
    { role: "user", content: `earlier question ${id}` },
    { role: "assistant", content: `earlier answer ${id}` },
  ],
  question: `Question ${id}?`,
  answer: `Answer ${id}.`,
  flagged: `no, I meant ${id}`,
  next_reply: `Next reply ${id}`,
  reviewer_doubt: `doubt ${id}`,
  guessed_reasons: ["off_topic"],
  ...extra,
});

const server = setupServer();
let queue: QueueItem[] = [];
let submitted: QueueSubmit | null = null;
let summaryCalls = 0;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  queue = [item(1), item(2), item(3)];
  submitted = null;
  useNavStore.setState({ feedbackBadge: 0 });
  server.use(
    http.get(`${API_BASE_URL}/feedback/queue`, () => HttpResponse.json(queue)),
    http.post(`${API_BASE_URL}/feedback/queue/submit`, async ({ request }) => {
      submitted = (await request.json()) as QueueSubmit;
      const ids = new Set(submitted.answers.map((a) => a.feedback_id));
      queue = queue.filter((q) => !ids.has(q.feedback_id));
      const confirmed = submitted.answers.filter((a) => a.confirmed).length;
      return HttpResponse.json({ confirmed, rejected: submitted.answers.length - confirmed });
    }),
    // The page's stage analysis (Task 11) loads alongside the queue.
    http.get(`${API_BASE_URL}/feedback/summary`, () => {
      summaryCalls += 1;
      return HttpResponse.json({
        kpis: {
          answers_with_feedback: 0,
          approval_rate: null,
          negatives_explicit: 0,
          negatives_implicit: 0,
          pending: queue.length,
        },
        stages: [],
        other_reasons: [],
      });
    }),
    http.get(`${API_BASE_URL}/feedback/presets`, () => HttpResponse.json([])),
  );
  summaryCalls = 0;
});
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

async function openQueue() {
  render(<FeedbackPage />);
  const start = await screen.findByRole("button", { name: "Start confirming" });
  await userEvent.click(start);
}

/** The questioned exchange currently shown (waits out the slide). */
const showsItem = (id: number) =>
  waitFor(() => expect(screen.getByText(`Question ${id}?`)).toBeInTheDocument());

describe("ConfirmQueue", () => {
  it("shows the banner with the queue count and syncs the badge", async () => {
    render(<FeedbackPage />);
    expect(await screen.findByText("3 items need your confirmation")).toBeInTheDocument();
    expect(useNavStore.getState().feedbackBadge).toBe(3);
  });

  it("hides the analysis region from assistive tech while expanded", async () => {
    render(<FeedbackPage />);
    const analysis = screen.getByTestId("feedback-analysis");
    expect(analysis).not.toHaveAttribute("aria-hidden");
    await userEvent.click(await screen.findByRole("button", { name: "Start confirming" }));
    expect(analysis).toHaveAttribute("aria-hidden", "true");
    await userEvent.click(screen.getByRole("button", { name: "Collapse" }));
    expect(analysis).not.toHaveAttribute("aria-hidden");
  });

  it("shows the full context of the questioned exchange", async () => {
    await openQueue();
    await showsItem(1);
    expect(screen.getByText("Session 1")).toBeInTheDocument();
    expect(screen.getByText("run #101")).toBeInTheDocument();
    expect(screen.getByText("earlier answer 1")).toBeInTheDocument();
    expect(screen.getByText("The questioned answer")).toBeInTheDocument();
    expect(screen.getByText("no, I meant 1")).toHaveAttribute("data-flagged", "true");
    expect(screen.getByText("Next reply 1")).toBeInTheDocument();
    expect(screen.getByText("Reviewer's doubt: doubt 1")).toBeInTheDocument();
  });

  it("answering advances to the next unanswered item", async () => {
    await openQueue();
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: /Yes, the answer was wrong/ }));
    await showsItem(2);
    expect(screen.getByRole("button", { name: "Item 1: yes, the answer was wrong" })).toBeInTheDocument();
  });

  it("← returns to the previous item and keeps its answer selected; revising works", async () => {
    await openQueue();
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: /Yes, the answer was wrong/ }));
    await showsItem(2);

    fireEvent.keyDown(document, { key: "ArrowLeft" });
    await showsItem(1);
    expect(screen.getByRole("button", { name: /Yes, the answer was wrong/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Revise to "no": the dot follows.
    await userEvent.click(screen.getByRole("button", { name: /No, I was adding/ }));
    expect(screen.getByRole("button", { name: "Item 1: no" })).toBeInTheDocument();
  });

  it("slides left on next and right on previous", async () => {
    await openQueue();
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: "Next (→)" }));
    expect(screen.getByTestId("queue-slide")).toHaveAttribute("data-slide", "out-left");
    expect(screen.getByTestId("queue-slide").className).toContain("-translate-x-8");
    await showsItem(2);
    await waitFor(() =>
      expect(screen.getByTestId("queue-slide")).toHaveAttribute("data-slide", "idle"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Previous (←)" }));
    expect(screen.getByTestId("queue-slide")).toHaveAttribute("data-slide", "out-right");
    expect(screen.getByTestId("queue-slide").className).not.toContain("-translate-x-8");
    expect(screen.getByTestId("queue-slide").className).toContain("translate-x-8");
  });

  it("ignores the arrow keys while typing in a field", async () => {
    await openQueue();
    await showsItem(1);
    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "ArrowRight" });
    expect(screen.getByTestId("queue-slide")).toHaveAttribute("data-slide", "idle");
    input.remove();
  });

  it("submits only the answered items and the queue shrinks", async () => {
    await openQueue();
    await showsItem(1);
    // Toggle a reason on item 1, then confirm it; reject item 2; leave 3.
    await userEvent.click(screen.getByRole("button", { name: /Factual error/ }));
    await userEvent.click(screen.getByRole("button", { name: /Yes, the answer was wrong/ }));
    await showsItem(2);
    await userEvent.click(screen.getByRole("button", { name: /No, I was adding/ }));
    await showsItem(3);

    const submit = screen.getByRole("button", { name: "Submit 2" });
    fireEvent.keyDown(document, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted!.answers).toEqual([
      { feedback_id: 1, confirmed: true, reasons: ["factual", "off_topic"] },
      { feedback_id: 2, confirmed: false, reasons: [] },
    ]);
    expect(await screen.findByText("1 item needs your confirmation")).toBeInTheDocument();
    expect(useNavStore.getState().feedbackBadge).toBe(1);
    expect(submit).toHaveTextContent("Submit 0");
    expect(submit).toBeDisabled();
    await showsItem(3);
    // The stage analysis refetches so the confirmed answers count.
    await waitFor(() => expect(summaryCalls).toBe(2));
  });

  it("shows an all-done message when the queue empties", async () => {
    queue = [item(1)];
    await openQueue();
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: /No, I was adding/ }));
    await userEvent.click(screen.getByRole("button", { name: "Submit 1" }));
    expect(await screen.findByText(/All done/)).toBeInTheDocument();
    expect(useNavStore.getState().feedbackBadge).toBe(0);
    // The body collapses with its content still rendered, then goes.
    const shown = screen.getByText("Question 1?");
    expect(shown.closest("[data-state]")).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(screen.queryByText("Question 1?")).not.toBeInTheDocument());
  });

  it("slides the remaining items in after a partial submit", async () => {
    await openQueue();
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: /No, I was adding/ }));
    await showsItem(2);
    const el = screen.getByTestId("queue-slide");
    await waitFor(() => expect(el).toHaveAttribute("data-slide", "idle"));
    const phases: string[] = [];
    const mo = new MutationObserver(() => phases.push(el.getAttribute("data-slide") ?? ""));
    mo.observe(el, { attributes: true, attributeFilter: ["data-slide"] });
    await userEvent.click(screen.getByRole("button", { name: "Submit 1" }));
    await waitFor(() => expect(phases).toEqual(["out-left", "in-right", "idle"]));
    mo.disconnect();
    expect(screen.getByText("Question 2?")).toBeInTheDocument();
    expect(screen.getByText("2 items need your confirmation")).toBeInTheDocument();
  });

  it("navigating during the post-submit slide doesn't cancel the swap", async () => {
    await openQueue();
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: /No, I was adding/ }));
    await showsItem(2);
    const el = screen.getByTestId("queue-slide");
    await waitFor(() => expect(el).toHaveAttribute("data-slide", "idle"));
    // Press → (and Collapse) the moment the post-submit slide starts.
    const mo = new MutationObserver(() => {
      if (el.getAttribute("data-slide") === "out-left") {
        mo.disconnect();
        fireEvent.keyDown(document, { key: "ArrowRight" });
        fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
      }
    });
    mo.observe(el, { attributes: true, attributeFilter: ["data-slide"] });
    await userEvent.click(screen.getByRole("button", { name: "Submit 1" }));
    expect(await screen.findByText("2 items need your confirmation")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Item 2: not answered yet" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Item 3: not answered yet" })).not.toBeInTheDocument();
    await waitFor(() => expect(el).toHaveAttribute("data-slide", "idle"));
    // Submitting works again.
    await userEvent.click(screen.getByRole("button", { name: /Yes, the answer was wrong/ }));
    const submit = screen.getByRole("button", { name: "Submit 1" });
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await waitFor(() => expect(submitted!.answers[0]!.feedback_id).toBe(2));
  });

  it("scrolls to the questioned exchange on every expand", async () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element,
    ) {
      return { top: this.hasAttribute("data-question") ? 500 : 0 } as DOMRect;
    });
    const set = vi.fn();
    vi.spyOn(Element.prototype, "scrollTop", "set").mockImplementation(set);
    await openQueue();
    await showsItem(1);
    await waitFor(() => expect(set).toHaveBeenCalledWith(488));
    const before = set.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Collapse" }));
    await userEvent.click(screen.getByRole("button", { name: "Start confirming" }));
    await waitFor(() => expect(set.mock.calls.length).toBeGreaterThan(before));
    expect(set).toHaveBeenLastCalledWith(488);
  });

  it("ignores Y/N key repeats, IME composition and open menus", async () => {
    await openQueue();
    await showsItem(1);
    fireEvent.keyDown(document, { key: "y", repeat: true });
    fireEvent.keyDown(document, { key: "y", isComposing: true });
    const menu = document.createElement("div");
    menu.setAttribute("role", "menu");
    menu.setAttribute("data-open", "");
    document.body.appendChild(menu);
    fireEvent.keyDown(document, { key: "y" });
    menu.remove();
    expect(screen.getByRole("button", { name: "Item 1: not answered yet" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "y" });
    expect(screen.getByRole("button", { name: "Item 1: yes, the answer was wrong" })).toBeInTheDocument();
  });

  it("offers 'Show full text' only for truncated text", async () => {
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return (this.textContent ?? "").includes("LONG") ? 200 : 20;
    });
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(40);
    // jsdom has no scrollIntoView; the expanded block reveals itself with it.
    const reveal = vi.fn();
    Element.prototype.scrollIntoView = reveal;
    queue = [item(1, { answer: "LONG answer that wraps over many lines" })];
    await openQueue();
    await showsItem(1);
    const answer = screen.getByText("LONG answer that wraps over many lines").closest("[data-clamp]")!;
    const toggle = await within(answer as HTMLElement).findByRole("button", {
      name: "Show full text",
    });
    // Only the long answer is truncated: one toggle on the page.
    expect(screen.getAllByRole("button", { name: "Show full text" })).toHaveLength(1);
    await userEvent.click(toggle);
    expect(within(answer as HTMLElement).getByRole("button", { name: "Show less" })).toBeInTheDocument();
    await waitFor(() => expect(reveal).toHaveBeenCalled());
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  });
  it("refetches the queue after a failed submit", async () => {
    await openQueue();
    await showsItem(1);
    await userEvent.click(screen.getByRole("button", { name: /No, I was adding/ }));
    await showsItem(2);
    // Item 1 left the queue elsewhere (e.g. rated with a thumbs): 404.
    queue = [item(2), item(3)];
    server.use(
      http.post(`${API_BASE_URL}/feedback/queue/submit`, () =>
        HttpResponse.json({ detail: "not in queue" }, { status: 404 }),
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "Submit 1" }));
    expect(await screen.findByText("2 items need your confirmation")).toBeInTheDocument();
    expect(useNavStore.getState().feedbackBadge).toBe(2);
    expect(screen.getByText("Question 2?")).toBeInTheDocument();
  });

  it("refetches when the nav badge changes while the page is open", async () => {
    render(<FeedbackPage />);
    expect(await screen.findByText("3 items need your confirmation")).toBeInTheDocument();
    queue = [...queue, item(4)];
    act(() => useNavStore.getState().setFeedbackBadge(4));
    expect(await screen.findByText("4 items need your confirmation")).toBeInTheDocument();
  });
});
