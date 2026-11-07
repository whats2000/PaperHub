import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { IdeasPage } from "@/pages/IdeasPage";
import { IDEATION_POLL_MS, useIdeationStore } from "@/store/ideation";
import type { GraphResponse, Idea, IdeaStatus, Problem } from "@/types/ideation";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const idea = (id: number, status: IdeaStatus, problemId: number | null = null): Idea => ({
  id,
  problem_id: problemId,
  source_doc: 1,
  source_section: "Method",
  source_chunk_ids: [10],
  target_doc: problemId === null ? 2 : null,
  target_section: problemId === null ? "Model" : null,
  target_chunk_ids: problemId === null ? [20] : [],
  title: `Idea ${id}`,
  rationale: "Because.",
  status,
  prev_status: null,
  note: "",
  dismiss_reasons: [],
  session_ids: [],
  created_at: "2026-11-05T10:00:00Z",
});

const problem = (
  id: number,
  text: string,
  count: number,
  status: Problem["status"] = "ready",
): Problem => ({
  id,
  text,
  tried: "",
  created_at: "2026-11-05T10:00:00Z",
  idea_count: count,
  status,
});

// Like the real API: a problem graph has only the documents its ideas touch
// (Doc A), while library_count / private_count always cover the whole library.
const graph = (ideas: Idea[], p: Problem | null = null): GraphResponse => ({
  documents: [
    { id: 1, title: "Doc A", domains: [1], idea_count: 2, is_private: false },
    ...(p ? [] : [{ id: 2, title: "Doc B", domains: [2], idea_count: 1, is_private: true }]),
  ],
  domains: [
    { id: 1, name: "Vision", color_index: 0 },
    ...(p ? [] : [{ id: 2, name: "Robotics", color_index: 1 }]),
  ],
  citations: [],
  library_count: 2,
  private_count: 1,
  ideas,
  problem: p,
});

// Explore: one idea per status; 5 in progress, 2 with a result, 1 dismissed.
const EXPLORE_IDEAS = [
  idea(1, "trying"),
  idea(2, "saved"),
  idea(3, "new"),
  idea(4, "worked"),
  idea(5, "failed"),
  idea(6, "dismissed"),
];

// The visible overlay text, not the sr-only live region.
const VISIBLE = "p:not([role])";

const server = setupServer();
let problems: Problem[] = [];
let posted: { text: string; tried: string } | null = null;
let problemPolls = 0;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  problems = [
    problem(1, "VLA new tasks cost too much", 3),
    problem(2, "Fine-grained retrieval ranks badly", 2),
  ];
  posted = null;
  problemPolls = 0;
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
  });
  server.use(
    http.get(`${API_BASE_URL}/ideation/explore/status`, () =>
      HttpResponse.json({ running: false, last_run_at: null, last_new_count: null, last_error: null }),
    ),
    http.get(`${API_BASE_URL}/ideation/problems`, () => HttpResponse.json(problems)),
    http.get(`${API_BASE_URL}/ideation/graph`, ({ request }) => {
      const pid = new URL(request.url).searchParams.get("problem_id");
      if (pid === null) return HttpResponse.json(graph(EXPLORE_IDEAS));
      const p = problems.find((x) => x.id === Number(pid)) ?? null;
      const ideas = p?.status === "ready" ? [idea(30, "new", p.id), idea(31, "new", p.id)] : [];
      return HttpResponse.json(graph(ideas, p));
    }),
  );
});
afterEach(() => {
  useIdeationStore.getState().stopPolling();
  server.resetHandlers();
  vi.useRealTimers();
});

const openSwitcher = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await screen.findByRole("button", { name: /^Problem:/ }));
  return screen.findByRole("listbox", { name: "Problems" });
};

describe("Ideas page: problem switcher", () => {
  it("lists free exploration, every problem with its idea count, and a new-problem row", async () => {
    const user = userEvent.setup();
    render(<IdeasPage />);
    await waitFor(() => expect(useIdeationStore.getState().graph).not.toBeNull());
    const menu = await openSwitcher(user);

    const rows = within(menu).getAllByRole("option");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Free exploration");
    // 5 non-dismissed explore ideas.
    expect(rows[0]).toHaveTextContent("5 ideas");
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
    expect(rows[1]).toHaveTextContent("VLA new tasks cost too much");
    expect(rows[1]).toHaveTextContent("3 ideas");
    expect(rows[2]).toHaveTextContent("Fine-grained retrieval ranks badly");
    expect(rows[2]).toHaveTextContent("2 ideas");
    expect(screen.getByRole("button", { name: "Describe a new problem" })).toBeInTheDocument();
  });

  it("switching to a problem loads its graph", async () => {
    const user = userEvent.setup();
    render(<IdeasPage />);
    const menu = await openSwitcher(user);
    await user.click(within(menu).getByRole("option", { name: /VLA new tasks/ }));
    await waitFor(() => expect(useIdeationStore.getState().graph?.problem?.id).toBe(1));
    expect(
      screen.getByRole("button", { name: "Problem: VLA new tasks cost too much" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("filter-count-active")).toHaveTextContent("2");
  });

  it("submit creates the problem, switches to it, shows generating, then ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    server.use(
      http.post(`${API_BASE_URL}/ideation/problems`, async ({ request }) => {
        posted = (await request.json()) as { text: string; tried: string };
        const p = problem(3, posted.text, 0, "generating");
        problems = [p, ...problems];
        return HttpResponse.json(p);
      }),
      http.get(`${API_BASE_URL}/ideation/problems/:id`, ({ params }) => {
        problemPolls += 1;
        const p = problems.find((x) => x.id === Number(params.id))!;
        // Ready on the second poll.
        if (problemPolls >= 2) Object.assign(p, { status: "ready", idea_count: 2 });
        return HttpResponse.json(p);
      }),
    );
    render(<IdeasPage />);
    await waitFor(() => expect(useIdeationStore.getState().graph).not.toBeNull());

    await user.type(
      screen.getByRole("textbox", { name: "Problem" }),
      "Transparent objects break grasping",
    );
    await user.click(screen.getByRole("button", { name: "What I already tried" }));
    await user.type(
      screen.getByRole("textbox", { name: "What I already tried" }),
      "Depth completion",
    );
    await user.click(screen.getByRole("button", { name: "Find cross-domain ideas" }));

    await waitFor(() =>
      expect(posted).toEqual({
        text: "Transparent objects break grasping",
        tried: "Depth completion",
      }),
    );
    expect(useIdeationStore.getState().scope).toBe(3);
    expect(
      await screen.findByText("Finding cross-domain ideas for your problem…", {
        selector: VISIBLE,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Problem: Transparent objects break grasping" }),
    ).toBeInTheDocument();
    // The entry box clears after a successful submit.
    expect(screen.getByRole("textbox", { name: "Problem" })).toHaveValue("");

    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(problemPolls).toBe(1);
    expect(
      screen.getByText("Finding cross-domain ideas for your problem…", { selector: VISIBLE }),
    ).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    await waitFor(() => expect(problemPolls).toBe(2));
    await waitFor(() => expect(useIdeationStore.getState().graph?.ideas).toHaveLength(2));
    await act(() => vi.advanceTimersByTimeAsync(300)); // exit animation
    expect(
      screen.queryByText("Finding cross-domain ideas for your problem…", { selector: VISIBLE }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("filter-count-active")).toHaveTextContent("2");

    // Polling stopped once ready.
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS * 3));
    expect(problemPolls).toBe(2);
  });

  it("filter chip counts match the fixture", async () => {
    render(<IdeasPage />);
    await waitFor(() => expect(screen.getByTestId("filter-count-active")).toHaveTextContent("5"));
    expect(screen.getByTestId("filter-count-saved")).toHaveTextContent("1");
    expect(screen.getByTestId("filter-count-trying")).toHaveTextContent("1");
    expect(screen.getByTestId("filter-count-done")).toHaveTextContent("2");
    expect(screen.getByTestId("filter-count-dismissed")).toHaveTextContent("1");
    expect(screen.getByRole("radio", { name: /In progress/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await userEvent.click(screen.getByRole("radio", { name: /Dismissed/ }));
    expect(useIdeationStore.getState().filter).toBe("dismissed");
    // Header meta counts the library and its private documents.
    expect(screen.getByText("Library: 2 documents (1 private)")).toBeInTheDocument();
  });
});

describe("Ideation store: problem polling", () => {
  it("stops on scope change, on unmount and on error", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    problems = [problem(7, "Slow", 0, "generating"), ...problems];
    let fail = false;
    server.use(
      http.get(`${API_BASE_URL}/ideation/problems/:id`, ({ params }) => {
        problemPolls += 1;
        if (fail) return new HttpResponse("boom", { status: 500 });
        return HttpResponse.json(problems.find((x) => x.id === Number(params.id)));
      }),
    );
    const store = useIdeationStore.getState();
    await store.load();
    store.setScope(7);
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(problemPolls).toBe(1);

    // Scope change cancels.
    useIdeationStore.getState().setScope("explore");
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS * 3));
    expect(problemPolls).toBe(1);

    // Returning resumes.
    useIdeationStore.getState().setScope(7);
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(problemPolls).toBe(2);

    // Unmounting the page cancels, including a load still in flight.
    const first = render(<IdeasPage />);
    first.unmount();
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS * 3));
    expect(problemPolls).toBe(2);
    const second = render(<IdeasPage />);
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(problemPolls).toBe(3);
    second.unmount();
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS * 3));
    expect(problemPolls).toBe(3);

    // Error stops polling and surfaces it.
    fail = true;
    useIdeationStore.getState().setScope("explore");
    useIdeationStore.getState().setScope(7);
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    const after = problemPolls;
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS * 3));
    expect(problemPolls).toBe(after);
    expect(useIdeationStore.getState().error).toMatch(/500/);
  });
});

describe("Ideation store: patchIdea", () => {
  it("replaces the idea in the loaded graph with the server's copy", async () => {
    server.use(
      http.patch(`${API_BASE_URL}/ideation/ideas/:id`, async ({ params, request }) => {
        const body = (await request.json()) as { status: IdeaStatus };
        return HttpResponse.json({ ...idea(Number(params.id), body.status), prev_status: "new" });
      }),
    );
    await useIdeationStore.getState().load();
    const out = await useIdeationStore.getState().patchIdea(3, { status: "saved" });
    expect(out.status).toBe("saved");
    const ideas = useIdeationStore.getState().graph!.ideas;
    expect(ideas.find((i) => i.id === 3)?.status).toBe("saved");
    expect(ideas).toHaveLength(EXPLORE_IDEAS.length);
  });
});

describe("Ideas page: fix round 1", () => {
  it("a poll error during generation swaps the spinner for an error with Retry, which re-polls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    problems = [problem(7, "Slow", 0, "generating"), ...problems];
    let fail = true;
    server.use(
      http.get(`${API_BASE_URL}/ideation/problems/:id`, ({ params }) => {
        problemPolls += 1;
        if (fail) return new HttpResponse("boom", { status: 500 });
        return HttpResponse.json(problems.find((x) => x.id === Number(params.id)));
      }),
    );
    useIdeationStore.setState({ scope: 7 });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<IdeasPage />);
    expect(
      await screen.findByText("Finding cross-domain ideas for your problem…", {
        selector: "p:not([role])",
      }),
    ).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(await screen.findByText("Couldn't load ideas.")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(300)); // exit animation
    expect(
      screen.queryByText("Finding cross-domain ideas for your problem…", {
        selector: "p:not([role])",
      }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("");

    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByText("Finding cross-domain ideas for your problem…", {
        selector: "p:not([role])",
      }),
    ).toBeInTheDocument();
    const before = problemPolls;
    await act(() => vi.advanceTimersByTimeAsync(IDEATION_POLL_MS));
    expect(problemPolls).toBe(before + 1);
    expect(useIdeationStore.getState().error).toBeNull();
  });

  it("the live region is mounted empty and filled while generating", async () => {
    problems = [problem(7, "Slow", 0, "generating"), ...problems];
    server.use(
      http.get(`${API_BASE_URL}/ideation/problems/:id`, () => HttpResponse.json(problems[0])),
    );
    render(<IdeasPage />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("");
    act(() => useIdeationStore.getState().setScope(7));
    await waitFor(() => expect(status).toHaveTextContent("Finding cross-domain ideas"));
  });

  it("keeps the header meta and shows neutral chip counts while a scope loads", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.get(`${API_BASE_URL}/ideation/graph`, async ({ request }) => {
        const pid = new URL(request.url).searchParams.get("problem_id");
        if (pid === null) return HttpResponse.json(graph(EXPLORE_IDEAS));
        await gate;
        return HttpResponse.json(graph([idea(30, "new", 1)], problems[0]));
      }),
    );
    render(<IdeasPage />);
    await waitFor(() => expect(screen.getByTestId("filter-count-active")).toHaveTextContent("5"));
    act(() => useIdeationStore.getState().setScope(1));
    expect(screen.getByText("Library: 2 documents (1 private)")).toBeInTheDocument();
    for (const f of ["active", "saved", "trying", "done", "dismissed"]) {
      expect(screen.getByTestId(`filter-count-${f}`)).toHaveTextContent("–");
    }
    release();
    await waitFor(() => expect(screen.getByTestId("filter-count-active")).toHaveTextContent("1"));
    expect(screen.getByText("Library: 2 documents (1 private)")).toBeInTheDocument();
  });

  it("a failed problem list shows inline in the switcher with Retry", async () => {
    let fail = true;
    server.use(
      http.get(`${API_BASE_URL}/ideation/problems`, () =>
        fail ? new HttpResponse("down", { status: 503 }) : HttpResponse.json(problems),
      ),
    );
    const user = userEvent.setup();
    render(<IdeasPage />);
    // The graph still loads, and its success doesn't mask the list error.
    await waitFor(() => expect(screen.getByTestId("filter-count-active")).toHaveTextContent("5"));
    expect(useIdeationStore.getState().listError).toMatch(/503/);
    await openSwitcher(user);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load problems");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    const menu = screen.getByRole("listbox", { name: "Problems" });
    await waitFor(() => expect(within(menu).getAllByRole("option")).toHaveLength(3));
    expect(screen.queryByText("Couldn't load problems")).not.toBeInTheDocument();
  });

  it("arrow keys move focus between the switcher options", async () => {
    const user = userEvent.setup();
    render(<IdeasPage />);
    await waitFor(() => expect(useIdeationStore.getState().problems).toHaveLength(2));
    const menu = await openSwitcher(user);
    const opts = within(menu).getAllByRole("option");
    expect(opts.map((o) => o.tabIndex)).toEqual([0, -1, -1]);
    opts[0]!.focus();
    await user.keyboard("{ArrowDown}");
    expect(opts[1]).toHaveFocus();
    await user.keyboard("{End}");
    expect(opts[2]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(opts[0]).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(opts[2]).toHaveFocus();
  });

  it("patchIdea keeps the switcher counts in step with dismiss and restore", async () => {
    server.use(
      http.patch(`${API_BASE_URL}/ideation/ideas/:id`, async ({ params, request }) => {
        const body = (await request.json()) as { status: IdeaStatus };
        return HttpResponse.json(
          idea(Number(params.id), body.status, Number(params.id) >= 30 ? 1 : null),
        );
      }),
    );
    const store = useIdeationStore.getState();
    await store.load();
    expect(useIdeationStore.getState().exploreIdeaCount).toBe(5);
    await useIdeationStore.getState().patchIdea(3, { status: "dismissed" });
    expect(useIdeationStore.getState().exploreIdeaCount).toBe(4);
    await useIdeationStore.getState().patchIdea(6, { status: "new" });
    expect(useIdeationStore.getState().exploreIdeaCount).toBe(5);

    useIdeationStore.getState().setScope(1);
    await waitFor(() => expect(useIdeationStore.getState().graph?.ideas).toHaveLength(2));
    const count = () => useIdeationStore.getState().problems.find((p) => p.id === 1)!.idea_count;
    expect(count()).toBe(3);
    await useIdeationStore.getState().patchIdea(30, { status: "dismissed" });
    expect(count()).toBe(2);
    await useIdeationStore.getState().patchIdea(30, { status: "saved" });
    expect(count()).toBe(3);
  });
});
