import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { StageAnalysis } from "@/components/feedback/StageAnalysis";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import type { FeedbackCase, FeedbackSummary, G1InboxRequest, PresetCreate } from "@/types/feedback";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// Backend order is the fixed STAGES order; the page ranks by total.
const summary: FeedbackSummary = {
  kpis: {
    answers_with_feedback: 40,
    approval_rate: 0.75,
    negatives_explicit: 12,
    negatives_implicit: 5,
    pending: 2,
  },
  stages: [
    { stage: "router", explicit: 1, implicit: 1, weekly: [0, 1, 1, 2], delta: 1 },
    { stage: "retrieval", explicit: 3, implicit: 1, weekly: [1, 1, 2, 1], delta: -1 },
    { stage: "citation", explicit: 6, implicit: 2, weekly: [2, 3, 4, 4], delta: 0 },
    { stage: "answer", explicit: 2, implicit: 1, weekly: [0, 0, 1, 2], delta: 1 },
  ],
  other_reasons: [
    { text: "Copied the table wrong", count: 3 },
    { text: "Too long", count: 1 },
  ],
  unattributed: { explicit: 2, implicit: 0 },
};

const cases: Record<string, FeedbackCase[]> = {
  citation: [
    {
      feedback_id: 11,
      run_id: 61,
      session_id: 7,
      question: "What causes model collapse?",
      kind: "explicit",
      created_at: "2026-11-05T10:00:00Z",
    },
    {
      feedback_id: 12,
      run_id: 57,
      session_id: 7,
      question: "How is capacity factor set?",
      kind: "implicit",
      created_at: "2026-11-04T10:00:00Z",
    },
  ],
  retrieval: [
    {
      feedback_id: 21,
      run_id: 49,
      session_id: 8,
      question: "I meant the second paper",
      kind: "implicit",
      created_at: "2026-11-03T10:00:00Z",
    },
  ],
};

const server = setupServer();
let g1Body: G1InboxRequest | null = null;
let presetBody: PresetCreate | null = null;
let summaryNow: FeedbackSummary = summary;

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  // jsdom has no scrollIntoView; expanded sections reveal themselves with it,
  // from a timer that can fire after a test has finished.
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  server.close();
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});
beforeEach(() => {
  g1Body = null;
  presetBody = null;
  summaryNow = summary;
  server.use(
    http.get(`${API_BASE_URL}/feedback/summary`, () => HttpResponse.json(summaryNow)),
    http.get(`${API_BASE_URL}/feedback/cases`, ({ request }) => {
      const stage = new URL(request.url).searchParams.get("stage") ?? "";
      return HttpResponse.json(cases[stage] ?? []);
    }),
    http.get(`${API_BASE_URL}/feedback/presets`, () => HttpResponse.json([])),
    http.post(`${API_BASE_URL}/feedback/presets`, async ({ request }) => {
      presetBody = (await request.json()) as PresetCreate;
      return HttpResponse.json({ label: presetBody.label });
    }),
    http.post(`${API_BASE_URL}/feedback/g1-inbox`, async ({ request }) => {
      g1Body = (await request.json()) as G1InboxRequest;
      return HttpResponse.json({ appended: g1Body.feedback_ids.length });
    }),
  );
});
afterEach(() => {
  server.resetHandlers();
  vi.clearAllMocks();
});

const stageList = () => screen.getByRole("listbox", { name: /stage/i });
const detail = () => screen.getByRole("region", { name: /cases/i });
const findDetail = () => screen.findByRole("region", { name: /cases/i });

describe("StageAnalysis", () => {
  it("shows the KPI row, with a dash for a null approval rate", async () => {
    summaryNow = { ...summary, kpis: { ...summary.kpis, approval_rate: null } };
    render(<StageAnalysis refreshKey={0} />);
    expect(await screen.findByText("40")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("17")).toBeInTheDocument(); // 12 + 5 negatives
  });

  it("formats the approval rate as a percentage", async () => {
    render(<StageAnalysis refreshKey={0} />);
    expect(await screen.findByText("75%")).toBeInTheDocument();
  });

  it("preselects the top stage and shows its cases", async () => {
    render(<StageAnalysis refreshKey={0} />);
    const options = await within(
      await screen.findByRole("listbox", { name: /stage/i }),
    ).findAllByRole("option");
    // Ranked by total: citation 8, retrieval 4, answer 3, router 2.
    expect(options.map((o) => o.getAttribute("data-stage"))).toEqual([
      "citation",
      "retrieval",
      "answer",
      "router",
    ]);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(
      await within(await findDetail()).findByText("What causes model collapse?"),
    ).toBeInTheDocument();
  });

  it("swaps the detail when another stage is selected", async () => {
    const user = userEvent.setup();
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    const retrieval = within(stageList()).getAllByRole("option")[1]!;
    await user.click(retrieval);
    expect(
      await within(await findDetail()).findByText("I meant the second paper"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(within(detail()).queryByText("What causes model collapse?")).not.toBeInTheDocument(),
    );
    expect(retrieval).toHaveAttribute("aria-selected", "true");
  });

  it("draws bar widths proportional to the counts", async () => {
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    const width = (stage: string, kind: string) =>
      parseFloat(
        stageList()
          .querySelector(`[data-stage="${stage}"] [data-seg="${kind}"]`)!
          .getAttribute("width")!,
      );
    // The longest total (citation, 8) spans the track; widths are % of it.
    expect(width("citation", "explicit")).toBeCloseTo(75); // 6 / 8
    expect(width("citation", "implicit")).toBeCloseTo(25); // 2 / 8
    expect(width("retrieval", "explicit")).toBeCloseTo(37.5); // 3 / 8
    expect(width("router", "implicit")).toBeCloseTo(12.5); // 1 / 8
  });

  it("shows the same numbers in the table view", async () => {
    const user = userEvent.setup();
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    await user.click(screen.getByRole("button", { name: /table/i }));
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    const cells = rows.map((r) =>
      within(r)
        .getAllByRole("cell")
        .map((c) => c.textContent),
    );
    expect(cells[0]).toEqual(["Citation", "6", "2", "8", "2 → 3 → 4 → 4", "0"]);
    expect(cells[1]).toEqual(["Paper or section lookup", "3", "1", "4", "1 → 1 → 2 → 1", "−1"]);
  });

  it("sends the checked cases to the G1 to-label inbox", async () => {
    const user = userEvent.setup();
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    const boxes = within(detail()).getAllByRole("checkbox");
    expect(boxes).toHaveLength(2);
    await user.click(boxes[1]!); // uncheck the second case
    await user.click(within(detail()).getByRole("button", { name: /G1 to-label \(1\)/ }));
    await waitFor(() => expect(g1Body).toEqual({ feedback_ids: [11] }));
    expect(toast.success).toHaveBeenCalledWith("Added 1 case to the G1 to-label inbox.");
    // Sent cases are unchecked, so they can't be appended twice by accident.
    const send = within(detail()).getByRole("button", { name: /G1 to-label \(0\)/ });
    expect(send).toBeDisabled();
    expect(
      within(detail())
        .getAllByRole("checkbox")
        .every((b) => !(b as HTMLInputElement).checked),
    ).toBe(true);
  });

  it("marks cases already in the G1 inbox, leaves them out of the count and says when nothing is new", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API_BASE_URL}/feedback/cases`, () =>
        HttpResponse.json(
          (cases.citation ?? []).map((c) => ({ ...c, in_g1_inbox: c.feedback_id === 11 })),
        ),
      ),
    );
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    expect(within(detail()).getAllByText("In G1 inbox")).toHaveLength(1);
    const boxes = within(detail()).getAllByRole("checkbox");
    expect(boxes[0]).toBeDisabled();
    expect((boxes[0] as HTMLInputElement).checked).toBe(false);
    expect(within(detail()).getByRole("button", { name: /G1 to-label \(1\)/ })).toBeEnabled();
    server.use(
      http.post(`${API_BASE_URL}/feedback/g1-inbox`, () =>
        HttpResponse.json({ appended: 0, already: 1 }),
      ),
    );
    await user.click(within(detail()).getByRole("button", { name: /G1 to-label \(1\)/ }));
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(
        "Nothing new: these cases are already in the G1 to-label inbox.",
      ),
    );
    // Now both are in the inbox: nothing left to send.
    expect(within(detail()).getByRole("button", { name: /G1 to-label \(0\)/ })).toBeDisabled();
  });

  it("counts negatives no stage claims in their own row and explains the counting", async () => {
    render(<StageAnalysis refreshKey={0} />);
    const row = await screen.findByTestId("no-stage-row");
    expect(row).toHaveTextContent("No stage (other / no reason)");
    expect(row).toHaveTextContent("2");
    expect(screen.getByText(/counted once in every stage/)).toBeInTheDocument();
    expect(screen.getByText(/Negative answers \(explicit 12/)).toBeInTheDocument();
  });

  it("toasts the appended count the backend reports", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API_BASE_URL}/feedback/g1-inbox`, () => HttpResponse.json({ appended: 2 })),
    );
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    await user.click(within(detail()).getByRole("button", { name: /G1 to-label \(2\)/ }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Added 2 cases to the G1 to-label inbox."),
    );
  });

  it("opens a case's trace inline under a single toggle", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API_BASE_URL}/sessions/7/runs/61/trace`, () =>
        HttpResponse.json([
          {
            run_id: 61,
            branch: "",
            step_index: 0,
            parent_step: null,
            agent: "router",
            tool: "router:classify",
            model: "m",
            args_redacted_json: null,
            result_summary_json: null,
            latency_ms: 12,
            token_in: null,
            token_out: null,
            status: "ok",
            error: null,
          },
        ]),
      ),
      http.get(`${API_BASE_URL}/sessions/7/runs/57/trace`, () => HttpResponse.json([])),
    );
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    const [first, second] = within(detail()).getAllByRole("button", { name: /view trace/i });
    await user.click(first!);
    expect(await within(detail()).findByText(/router:classify/)).toBeInTheDocument();
    // TraceInline's own "Trace" header is hidden: the case's link is the toggle.
    expect(within(detail()).queryByRole("button", { name: /^Trace/ })).not.toBeInTheDocument();
    expect(first).toHaveAttribute("aria-expanded", "true");
    // An empty trace shows the empty state rather than nothing.
    await user.click(second!);
    expect(await within(detail()).findByText("No steps recorded.")).toBeInTheDocument();
  });

  it("refetches sessions once for a case not mirrored locally", async () => {
    const user = userEvent.setup();
    useChatStore.setState({ sessions: [], activeSessionId: null });
    useNavStore.setState({ page: "feedback" });
    server.use(
      http.get(`${API_BASE_URL}/sessions`, () =>
        HttpResponse.json([
          { id: 7, title: "Collapse", created_at: "", updated_at: "", message_count: 2 },
        ]),
      ),
    );
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    await user.click(within(detail()).getAllByRole("button", { name: /view conversation/i })[0]!);
    await waitFor(() => expect(useNavStore.getState().page).toBe("chat"));
    const active = useChatStore.getState().activeSessionId;
    expect(useChatStore.getState().sessions.find((s) => s.id === active)?.backend_session_id).toBe(
      7,
    );
  });

  it("says a deleted conversation is gone instead of opening a ghost", async () => {
    const user = userEvent.setup();
    useChatStore.setState({ sessions: [], activeSessionId: null });
    useNavStore.setState({ page: "feedback" });
    server.use(http.get(`${API_BASE_URL}/sessions`, () => HttpResponse.json([])));
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    await user.click(within(detail()).getAllByRole("button", { name: /view conversation/i })[0]!);
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("This conversation is no longer available."),
    );
    expect(useChatStore.getState().sessions).toHaveLength(0);
    expect(useNavStore.getState().page).toBe("feedback");
  });

  it("opens the conversation of a case in the chat page", async () => {
    const user = userEvent.setup();
    useChatStore.setState({
      sessions: [{ id: 3, title: "Collapse", messages: [], backend_session_id: 7 }],
      activeSessionId: null,
    });
    useNavStore.setState({ page: "feedback" });
    render(<StageAnalysis refreshKey={0} />);
    await within(await findDetail()).findByText("What causes model collapse?");
    await user.click(within(detail()).getAllByRole("button", { name: /view conversation/i })[0]!);
    await waitFor(() => expect(useChatStore.getState().activeSessionId).toBe(3));
    expect(useNavStore.getState().page).toBe("chat");
  });

  it("promotes an other reason to a preset with an inline stage picker", async () => {
    const user = userEvent.setup();
    render(<StageAnalysis refreshKey={0} />);
    const row = (await screen.findByText("Copied the table wrong")).closest("li")!;
    expect(within(row).getByText(/3/)).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: /promote to preset/i }));
    await user.click(await within(row).findByRole("button", { name: "Answer content" }));
    await waitFor(() =>
      expect(presetBody).toEqual({ label: "Copied the table wrong", stage: "answer" }),
    );
    expect(toast.success).toHaveBeenCalled();
  });

  it("marks a duplicate promotion as already a preset", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API_BASE_URL}/feedback/presets`, () =>
        HttpResponse.json({ detail: "exists" }, { status: 409 }),
      ),
    );
    render(<StageAnalysis refreshKey={0} />);
    const row = (await screen.findByText("Copied the table wrong")).closest("li")!;
    await user.click(within(row).getByRole("button", { name: /promote to preset/i }));
    await user.click(await within(row).findByRole("button", { name: "Routing" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("That reason is already a preset."),
    );
    expect(within(row).getByText("Added")).toBeInTheDocument();
    expect(
      within(row).queryByRole("button", { name: /promote to preset/i }),
    ).not.toBeInTheDocument();
  });
});
