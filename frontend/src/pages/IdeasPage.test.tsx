import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const { toast } = vi.hoisted(() => ({
  toast: Object.assign(vi.fn((..._args: unknown[]) => "toast-1"), {
    success: vi.fn(),
    error: vi.fn(),
    dismiss: vi.fn(),
  }),
}));
vi.mock("sonner", () => ({ toast }));

import { API_BASE_URL } from "@/lib/api";
import { IdeasPage } from "@/pages/IdeasPage";
import { useChatStore } from "@/store/chat";
import { IDEATION_POLL_MS, useIdeationStore } from "@/store/ideation";
import { useNavStore } from "@/store/nav";
import type { ExploreStatus, GraphResponse, Idea, IdeaPatch, Problem } from "@/types/ideation";

/**
 * Plan H3 end to end on the Ideas page, against an MSW backend that keeps
 * state like the real API: describe a problem → generating → ideas drawn →
 * select → save → dismiss → undo → open chat; plus free exploration's
 * "find new pairs" run (Ruling U3).
 */

const PROBLEM_TEXT = "Rerank retrieved images for a product search engine";

const DOCS: GraphResponse["documents"] = [
  { id: 1, title: "Faster R-CNN", domains: [1], idea_count: 1, is_private: false },
  { id: 2, title: "GPT-3", domains: [2], idea_count: 1, is_private: false },
];
const DOMAINS: GraphResponse["domains"] = [
  { id: 1, name: "Vision", color_index: 0 },
  { id: 2, name: "Language", color_index: 1 },
];

const problemIdea = (id: number, source: number, title: string): Idea => ({
  id,
  problem_id: 3,
  source_doc: source,
  source_section: "Region Proposal Networks",
  source_chunk_ids: [10, 11],
  target_doc: null,
  target_section: null,
  target_chunk_ids: [],
  title,
  rationale: "Score candidates against a fixed set of reference anchors.",
  status: "new",
  prev_status: null,
  note: "",
  dismiss_reasons: [],
  session_ids: [],
  created_at: "2026-11-06T10:00:00Z",
});

const exploreIdea = (id: number): Idea => ({
  ...problemIdea(id, 1, `Pair ${id}`),
  problem_id: null,
  target_doc: 2,
  target_section: "Few-shot prompting",
  target_chunk_ids: [20],
});

// ---- Server state, mutated by the handlers like the real API.
let problems: Problem[];
let ideas: Idea[];
let exploreIdeas: Idea[];
let problemPolls: number;
let posted: { text: string; tried: string } | null;
let patches: IdeaPatch[];
let undoCalls: number[];
let chatCalls: number[];
let exploreStarts: number;
let statusPolls: number;
/** The explore status the n-th poll returns (the last entry repeats). */
let exploreStatuses: ExploreStatus[];
/** A run is already in flight server-side before the page loads. */
let serverExploring: boolean;
let retryCalls: number[];
const IDLE: ExploreStatus = { running: false, last_run_at: null, last_new_count: null, last_error: null };

const graphFor = (pid: string | null): GraphResponse => {
  if (pid === null)
    return { documents: DOCS, domains: DOMAINS, citations: [], ideas: exploreIdeas, problem: null, library_count: 2, private_count: 0 };
  const p = problems.find((x) => x.id === Number(pid)) ?? null;
  return {
    documents: DOCS,
    domains: DOMAINS,
    citations: [],
    library_count: 2,
    private_count: 0,
    ideas: p?.status === "ready" ? ideas : [],
    problem: p,
  };
};

const server = setupServer(
  http.get(`${API_BASE_URL}/ideation/problems`, () => HttpResponse.json(problems)),
  http.get(`${API_BASE_URL}/ideation/graph`, ({ request }) =>
    HttpResponse.json(graphFor(new URL(request.url).searchParams.get("problem_id"))),
  ),
  http.post(`${API_BASE_URL}/ideation/problems`, async ({ request }) => {
    posted = (await request.json()) as { text: string; tried: string };
    const p: Problem = {
      id: 3,
      text: posted.text,
      tried: posted.tried,
      created_at: "2026-11-06T10:00:00Z",
      idea_count: 0,
      status: "generating",
    };
    problems = [p, ...problems];
    return HttpResponse.json(p);
  }),
  http.get(`${API_BASE_URL}/ideation/problems/:id`, ({ params }) => {
    problemPolls += 1;
    const p = problems.find((x) => x.id === Number(params.id))!;
    // The generation finishes by the second poll.
    if (problemPolls >= 2) Object.assign(p, { status: "ready", idea_count: ideas.length });
    return HttpResponse.json(p);
  }),
  http.patch(`${API_BASE_URL}/ideation/ideas/:id`, async ({ request, params }) => {
    const body = (await request.json()) as IdeaPatch;
    patches.push(body);
    const id = Number(params.id);
    ideas = ideas.map((i) => {
      if (i.id !== id) return i;
      if (body.status === "dismissed") {
        return { ...i, prev_status: i.status, status: "dismissed", dismiss_reasons: body.dismiss_reasons ?? [] };
      }
      return { ...i, ...(body.status ? { status: body.status } : {}) };
    });
    return HttpResponse.json(ideas.find((i) => i.id === id));
  }),
  http.post(`${API_BASE_URL}/ideation/ideas/:id/undo-dismiss`, ({ params }) => {
    const id = Number(params.id);
    undoCalls.push(id);
    ideas = ideas.map((i) =>
      i.id === id ? { ...i, status: i.prev_status ?? "new", prev_status: null, dismiss_reasons: [] } : i,
    );
    return HttpResponse.json(ideas.find((i) => i.id === id));
  }),
  http.post(`${API_BASE_URL}/ideation/ideas/:id/chat`, ({ params }) => {
    chatCalls.push(Number(params.id));
    return HttpResponse.json({
      session_id: 700,
      title: "Anchor-based reranking",
      prefill: "Let's dig into the idea “Anchor-based reranking”.",
      paper_content_ids: [1],
    });
  }),
  http.post(`${API_BASE_URL}/ideation/problems/:id/retry`, ({ params }) => {
    const id = Number(params.id);
    retryCalls.push(id);
    const p = problems.find((x) => x.id === id)!;
    if (p.status !== "error") return new HttpResponse("not failed", { status: 409 });
    p.status = "generating";
    problemPolls = 0;
    return HttpResponse.json(p);
  }),
  http.post(`${API_BASE_URL}/ideation/explore`, () => {
    exploreStarts += 1;
    return HttpResponse.json({ started: true });
  }),
  http.get(`${API_BASE_URL}/ideation/explore/status`, () => {
    // Like the real API: no run started (here or before load) = idle.
    if (exploreStarts === 0 && !serverExploring) return HttpResponse.json(IDLE);
    const s = exploreStatuses[Math.min(statusPolls, exploreStatuses.length - 1)]!;
    statusPolls += 1;
    // The run's new ideas are in the graph once it has finished.
    if (!s.running && s.last_new_count) exploreIdeas = [exploreIdea(40), exploreIdea(41)];
    return HttpResponse.json(s);
  }),
);

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  server.listen({ onUnhandledRequest: "error" });
});
afterAll(() => server.close());
beforeEach(() => {
  problems = [];
  ideas = [problemIdea(21, 1, "Anchor-based reranking"), problemIdea(22, 2, "Prompt the reranker with examples")];
  exploreIdeas = [];
  problemPolls = 0;
  posted = null;
  patches = [];
  undoCalls = [];
  chatCalls = [];
  exploreStarts = 0;
  statusPolls = 0;
  exploreStatuses = [];
  serverExploring = false;
  retryCalls = [];
  toast.mockClear();
  toast.success.mockClear();
  toast.error.mockClear();
  toast.dismiss.mockClear();
  useIdeationStore.setState({
    problems: [],
    scope: "explore",
    view: "graph",
    filter: "active",
    selected: null,
    graph: null,
    exploreIdeaCount: null,
    libraryDocs: null,
    libraryPrivate: null,
    loading: false,
    error: null,
    listError: null,
    submitting: false,
    exploring: false,
    exploreStatus: null,
    leavingIdeaIds: [],
    undoingIdeaIds: [],
  });
  useNavStore.setState({ page: "ideas" });
  useChatStore.setState({ sessions: [], activeSessionId: null, composerDraft: "" });
});
afterEach(() => {
  useIdeationStore.getState().stopPolling();
  server.resetHandlers();
  vi.useRealTimers();
});

// The visible overlay text, not the sr-only live region.
const VISIBLE = "p:not([role])";

describe("Ideas page end to end", () => {
  it("describe a problem → generating → ideas drawn → select → save → dismiss → undo → open chat", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<IdeasPage />);
    await waitFor(() => expect(useIdeationStore.getState().graph).not.toBeNull());

    // Describe a problem.
    await user.type(screen.getByRole("textbox", { name: "Problem" }), PROBLEM_TEXT);
    await user.click(screen.getByRole("button", { name: "Find cross-domain ideas" }));
    await waitFor(() => expect(posted).toEqual({ text: PROBLEM_TEXT, tried: "" }));

    // Generating.
    expect(
      await screen.findByText("Finding cross-domain ideas for your problem…", { selector: VISIBLE }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Problem: ${PROBLEM_TEXT}` })).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(problemPolls).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));

    // Ideas drawn on the graph, the overlay gone.
    await waitFor(() => expect(screen.getByTestId("idea-21")).toBeInTheDocument());
    expect(screen.getByTestId("idea-22")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(300)); // the overlay's exit
    expect(
      screen.queryByText("Finding cross-domain ideas for your problem…", { selector: VISIBLE }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("filter-count-active")).toHaveTextContent("2");
    // Nothing is selected on entry.
    expect(screen.queryByTestId("idea-drawer")).toBeNull();

    // Select an idea: the drawer opens on it.
    fireEvent.click(screen.getByTestId("idea-21"));
    const drawer = await screen.findByTestId("idea-drawer");
    expect(drawer).toHaveAttribute("data-state", "open");
    expect(within(drawer).getByText("Anchor-based reranking")).toBeInTheDocument();

    // Save.
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByTestId("filter-count-saved")).toHaveTextContent("1"));
    expect(patches).toEqual([{ status: "saved" }]);
    expect(toast.success).toHaveBeenCalled();

    // Dismiss with a reason: it leaves the active view and the drawer closes.
    await user.click(within(drawer).getByRole("button", { name: "Dismiss" }));
    await user.click(within(drawer).getByRole("button", { name: "Not novel" }));
    await user.click(within(drawer).getByRole("button", { name: "Dismiss idea" }));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(patches[1]).toEqual({ status: "dismissed", dismiss_reasons: ["not_novel"], dismiss_note: "" });
    expect(useIdeationStore.getState().selected).toBeNull();
    expect(screen.getByTestId("filter-count-active")).toHaveTextContent("1");
    expect(screen.getByTestId("filter-count-dismissed")).toHaveTextContent("1");
    expect(useIdeationStore.getState().problems[0]?.idea_count).toBe(1);

    // Undo from the toast: the exact previous status (saved, not new) is back, and selected.
    const [, opts] = toast.mock.calls[0] as [string, { action: { label: string; onClick: () => void } }];
    expect(opts.action.label).toBe("Undo");
    act(() => opts.action.onClick());
    await waitFor(() => expect(screen.getByTestId("filter-count-saved")).toHaveTextContent("1"));
    expect(undoCalls).toEqual([21]);
    expect(patches).toHaveLength(2);
    expect(screen.getByTestId("filter-count-active")).toHaveTextContent("2");
    expect(useIdeationStore.getState().selected).toEqual({ kind: "idea", id: 21 });
    const reopened = await screen.findByTestId("idea-drawer");

    // Open a chat: a linked session on the chat page, the opener in the composer.
    await user.click(within(reopened).getByRole("button", { name: "Open a chat" }));
    await waitFor(() => expect(useNavStore.getState().page).toBe("chat"));
    expect(chatCalls).toEqual([21]);
    const chat = useChatStore.getState();
    const session = chat.sessions.find((s) => s.backend_session_id === 700);
    expect(session).toMatchObject({ title: "Anchor-based reranking", idea_id: 21 });
    expect(chat.activeSessionId).toBe(session?.id);
    expect(chat.composerDraft).toContain("Let's dig into the idea “Anchor-based reranking”.");
    expect(chat.composerDraft).toContain(`My problem: ${PROBLEM_TEXT}`);
  });
});

describe("Ideas page: free exploration (find new pairs)", () => {
  const running: ExploreStatus = { running: true, last_run_at: null, last_new_count: null, last_error: null };

  async function startExplore() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const view = render(<IdeasPage />);
    await waitFor(() => expect(useIdeationStore.getState().graph).not.toBeNull());
    await user.click(screen.getByRole("button", { name: "Find new pairs" }));
    await waitFor(() => expect(exploreStarts).toBe(1));
    return view;
  }

  it("polls the status until the run stops, then redraws the graph with the new pairs", async () => {
    exploreStatuses = [
      running,
      { running: false, last_run_at: "2026-11-06T10:01:00Z", last_new_count: 2, last_error: null },
    ];
    await startExplore();
    expect(screen.getByRole("button", { name: "Finding pairs…" })).toBeDisabled();
    expect(
      await screen.findByText("Finding cross-domain pairs in your library…", { selector: VISIBLE }),
    ).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(statusPolls).toBe(1);
    expect(screen.queryByTestId("idea-40")).toBeNull();

    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    await waitFor(() => expect(screen.getByTestId("idea-40")).toBeInTheDocument());
    expect(screen.getByTestId("idea-41")).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith("2 new pairs");
    expect(toast).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Find new pairs" })).toBeEnabled();

    // Polling stopped once the run finished.
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS * 3));
    expect(statusPolls).toBe(2);
  });

  it("says “No new pairs this time” when the re-run kept nothing new", async () => {
    exploreStatuses = [{ running: false, last_run_at: "2026-11-06T10:01:00Z", last_new_count: 0, last_error: null }];
    await startExplore();
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("No new pairs this time"));
    expect(toast.success).not.toHaveBeenCalled();
    expect(statusPolls).toBe(1);
  });

  it("toasts a failed run", async () => {
    exploreStatuses = [{ running: false, last_run_at: "2026-11-06T10:01:00Z", last_new_count: null, last_error: "boom" }];
    await startExplore();
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Free exploration failed"));
  });

  it("stops polling on unmount", async () => {
    exploreStatuses = [running];
    const { unmount } = await startExplore();
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(statusPolls).toBe(1);
    unmount();
    expect(useIdeationStore.getState().exploring).toBe(false);
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS * 3));
    expect(statusPolls).toBe(1);
    expect(toast).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("is only offered in the free-exploration scope", async () => {
    problems = [
      { id: 3, text: PROBLEM_TEXT, tried: "", created_at: "2026-11-06T10:00:00Z", idea_count: 2, status: "ready" },
    ];
    render(<IdeasPage />);
    expect(await screen.findByRole("button", { name: "Find new pairs" })).toBeInTheDocument();
    act(() => useIdeationStore.getState().setScope(3));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Find new pairs" })).not.toBeInTheDocument(),
    );
  });
});

describe("Ideas page: run feedback (retry, progress, skipped documents)", () => {
  const failedProblem = (): Problem => ({
    id: 3,
    text: PROBLEM_TEXT,
    tried: "",
    created_at: "2026-11-06T10:00:00Z",
    idea_count: 0,
    status: "error",
  });

  it("a failed problem offers Retry, which restarts generation and the poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    problems = [failedProblem()];
    useIdeationStore.setState({ scope: 3 });
    render(<IdeasPage />);
    expect(await screen.findByText("Generating ideas for this problem failed.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retryCalls).toEqual([3]));
    expect(
      await screen.findByText("Finding cross-domain ideas for your problem…", { selector: VISIBLE }),
    ).toBeInTheDocument();
    // The poll runs again until the problem is ready, then the ideas are drawn.
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    await waitFor(() => expect(screen.getByTestId("idea-21")).toBeInTheDocument());
  });

  it("shows card progress while the library's documents are summarised", async () => {
    problems = [
      { ...failedProblem(), status: "generating", cards_ready: 4, cards_total: 49, skipped_docs: 0 },
    ];
    useIdeationStore.setState({ scope: 3 });
    render(<IdeasPage />);
    expect(await screen.findByTestId("ideas-generating-sub")).toHaveTextContent(
      "Summarising your documents — 4/49",
    );
    expect(screen.getByTestId("ideas-generating-sub")).not.toHaveTextContent("a minute");
  });

  it("does not say it summarises documents once every card is ready", async () => {
    problems = [
      { ...failedProblem(), status: "generating", cards_ready: 61, cards_total: 61, skipped_docs: 0 },
    ];
    useIdeationStore.setState({ scope: 3 });
    render(<IdeasPage />);
    const sub = await screen.findByTestId("ideas-generating-sub");
    expect(sub).toHaveTextContent("Reading the mechanism cards of your library");
    expect(sub).not.toHaveTextContent(/summaris/i);
  });

  it("says plainly when nothing in the library is relevant to the problem", async () => {
    problems = [{ ...failedProblem(), status: "ready", idea_count: 0, nothing_relevant: true, nothing_relevant_reason: "No robotics papers." }];
    ideas = [];
    useIdeationStore.setState({ scope: 3 });
    render(<IdeasPage />);
    const hint = await screen.findByTestId("ideas-empty-hint");
    expect(within(hint).getByText("Nothing in your library is relevant to this problem.")).toBeInTheDocument();
    expect(within(hint).getByTestId("ideas-no-relevant")).toHaveTextContent("No robotics papers.");
    expect(within(hint).queryByText("No ideas for this problem yet.")).toBeNull();
  });

  it("says how many documents couldn't be analysed", async () => {
    problems = [{ ...failedProblem(), status: "ready", idea_count: 2, skipped_docs: 2 }];
    useIdeationStore.setState({ scope: 3 });
    render(<IdeasPage />);
    const note = await screen.findByText("2 documents couldn't be analysed");
    await waitFor(() => expect(note.closest("[data-state]")).toHaveAttribute("data-state", "open"));
  });

  it("resumes the poll of an explore run that was already running on load", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serverExploring = true;
    exploreStatuses = [
      { ...IDLE, running: true, cards_ready: 3, cards_total: 10 },
      { ...IDLE, running: true, cards_ready: 3, cards_total: 10 },
      { ...IDLE, last_run_at: "2026-11-06T10:01:00Z", last_new_count: 2 },
    ];
    render(<IdeasPage />);
    await waitFor(() => expect(useIdeationStore.getState().exploring).toBe(true));
    expect(screen.getByRole("button", { name: "Finding pairs…" })).toBeDisabled();
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(await screen.findByTestId("ideas-generating-sub")).toHaveTextContent(
      "Summarising your documents — 3/10",
    );
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    await waitFor(() => expect(screen.getByTestId("idea-40")).toBeInTheDocument());
    expect(useIdeationStore.getState().exploring).toBe(false);
    expect(exploreStarts).toBe(0);
  });
});

describe("Ideas page: an explore scope with no ideas yet", () => {
  it("keeps the library's documents and domains in view under a small hint, not a veil", async () => {
    render(<IdeasPage />);
    const hint = await screen.findByTestId("ideas-empty-hint");
    expect(within(hint).getByText("No cross-domain pairs yet.")).toBeInTheDocument();
    expect(
      within(hint).getByText("Documents are grouped by domain. Click one to see or edit its domains."),
    ).toBeInTheDocument();
    // A compact hint that lets clicks through, not a full-cover veil.
    expect(hint.className).toContain("pointer-events-none");
    expect(hint.className).not.toMatch(/\binset-0\b/);
    expect(hint.className).not.toMatch(/bg-background\/80/);
    // The documents and their domain names are drawn.
    expect(screen.getByTestId("node-1")).toBeInTheDocument();
    expect(screen.getByTestId("hull-label-1")).toHaveTextContent("Vision");
    expect(screen.getByTestId("hull-label-2")).toHaveTextContent("Language");
  });
});
