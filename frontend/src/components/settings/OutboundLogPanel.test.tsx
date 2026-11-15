import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL, type HeldSearch, type OutboundLogRow } from "../../lib/api";
import { useChatStore } from "../../store/chat";
import { useNavStore } from "../../store/nav";
import { OutboundLogPanel } from "./OutboundLogPanel";

const { toastFn, toastError } = vi.hoisted(() => ({ toastFn: vi.fn(), toastError: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: toastError, success: vi.fn() }),
}));

const row = (o: Partial<OutboundLogRow>): OutboundLogRow => ({
  id: 1,
  created_at: "2026-11-13T02:46:00+00:00",
  owner_id: 1,
  actor: "agent",
  caller: "paper_search",
  kind: "search",
  destination: "api.semanticscholar.org",
  sent_text: "sparsity segmentation",
  original_text: null,
  decision: "sent",
  stage: "review",
  reason: null,
  run_id: null,
  session_id: null,
  ...o,
});
const held = (o: Partial<HeldSearch>): HeldSearch => ({
  id: "h1",
  created_at: "2026-11-13T01:00:00+00:00",
  caller: "trending",
  kind: "search",
  destination: "api.semanticscholar.org",
  text: "wafer defect sparse",
  reason: "Review timed out",
  unattended: true,
  run_id: null,
  session_id: null,
  ...o,
});

let logRows: OutboundLogRow[] = [];
let heldRows: HeldSearch[] = [];
let logUrls: URL[] = [];
let csvUrls: URL[] = [];
let calls: string[] = [];
let logStatus = 200;
let heldStatus = 200;
let sendResult: { status: number; body: Record<string, unknown> } = { status: 200, body: {} };

const server = setupServer(
  http.get(`${API_BASE_URL}/outbound/log`, ({ request }) => {
    logUrls.push(new URL(request.url));
    if (logStatus !== 200) return HttpResponse.json({ detail: "x" }, { status: logStatus });
    return HttpResponse.json(logRows);
  }),
  http.get(`${API_BASE_URL}/outbound/log.csv`, ({ request }) => {
    csvUrls.push(new URL(request.url));
    return new HttpResponse("id\n1\n", { headers: { "Content-Type": "text/csv" } });
  }),
  http.get(`${API_BASE_URL}/outbound/held`, () =>
    heldStatus !== 200 ? HttpResponse.json({}, { status: heldStatus }) : HttpResponse.json(heldRows),
  ),
  http.post(`${API_BASE_URL}/outbound/held/:id/send`, ({ params }) => {
    calls.push(`send:${String(params.id)}`);
    return HttpResponse.json(sendResult.body, { status: sendResult.status });
  }),
  http.post(`${API_BASE_URL}/outbound/held/:id/discard`, ({ params }) => {
    calls.push(`discard:${String(params.id)}`);
    return HttpResponse.json({ status: "discarded", held_ids: [String(params.id), "h2"] });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  logRows = [];
  heldRows = [];
  logUrls = [];
  csvUrls = [];
  calls = [];
  logStatus = 200;
  heldStatus = 200;
  toastFn.mockReset();
  toastError.mockReset();
  sendResult = { status: 200, body: { status: "sent", held_id: "h1", held_ids: ["h1"], text: "x" } };
  useNavStore.setState({ heldBadge: 0, page: "ideas", focusHeldId: null });
  useChatStore.setState({ sessions: [], activeSessionId: null });
});
afterEach(() => server.resetHandlers());

describe("OutboundLogPanel log tab", () => {
  it("shows a rewritten row with the original struck through", async () => {
    logRows = [
      row({
        decision: "rewritten",
        sent_text: "sparsity segmentation",
        original_text: "sparsity KESTREL segmentation",
      }),
    ];
    render(<OutboundLogPanel />);
    const sent = await screen.findByText("sparsity segmentation");
    const orig = screen.getByText("sparsity KESTREL segmentation");
    expect(orig).toHaveClass("line-through");
    expect(sent).not.toHaveClass("line-through");
    expect(within(screen.getByRole("list", { name: "Everything sent" })).getByText("Rewritten")).toBeInTheDocument();
  });

  it("shows a blocked row's query unstruck, labelled as not sent", async () => {
    logRows = [
      row({ decision: "blocked", sent_text: null, original_text: "KESTREL v3 dataset", stage: "terms" }),
    ];
    render(<OutboundLogPanel />);
    const q = await screen.findByText("KESTREL v3 dataset");
    expect(q).not.toHaveClass("line-through");
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "Everything sent" })).getByText("Blocked").closest("span.dec"),
    ).toHaveClass("text-destructive");
    expect(screen.getByText("Sensitive term")).toBeInTheDocument();
  });

  it("maps stages and marks model calls as not logged", async () => {
    logRows = [
      row({ id: 2, kind: "llm", sent_text: null, stage: "llm", destination: "gemini" }),
      row({ id: 3, stage: "exact_id", sent_text: "id 2511.04217" }),
      row({ id: 4, stage: "span", decision: "held", sent_text: null }),
    ];
    render(<OutboundLogPanel />);
    expect(await screen.findByText("(content not logged)")).toBeInTheDocument();
    expect(screen.getByText("Not reviewed (model call)")).toBeInTheDocument();
    expect(screen.getByText("Exact id, no text")).toBeInTheDocument();
    expect(screen.getByText("Private passage or title")).toBeInTheDocument();
  });

  it("stacks each entry as a wrapping list row, never a wide table", async () => {
    logRows = [row({ id: 1 }), row({ id: 2, sent_text: "second query" })];
    render(<OutboundLogPanel />);
    const list = await screen.findByRole("list", { name: "Everything sent" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(list.closest(".overflow-x-auto")).toBeNull();
    expect(list.querySelector("[class*='min-w-[']")).toBeNull();
  });

  it("links a row's chat, opening that chat; a row without one says No chat", async () => {
    useChatStore.setState({
      sessions: [{ id: 5, title: "Wafer chat", messages: [], backend_session_id: 77 }],
      activeSessionId: null,
    });
    logRows = [row({ id: 1, session_id: 77 }), row({ id: 2, sent_text: "loose", session_id: null })];
    render(<OutboundLogPanel />);
    const link = await screen.findByRole("button", { name: "Wafer chat" });
    expect(screen.getByText("No chat")).toBeInTheDocument();
    await userEvent.setup().click(link);
    expect(useChatStore.getState().activeSessionId).toBe(5);
    expect(useNavStore.getState().page).toBe("chat");
    expect(useNavStore.getState().focusHeldId).toBeNull();
  });

  it("puts the chosen filters in the query string", async () => {
    logRows = [row({})];
    render(<OutboundLogPanel />);
    await screen.findByText("sparsity segmentation");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Decision"), "blocked");
    await waitFor(() => expect(logUrls.at(-1)?.searchParams.get("decision")).toBe("blocked"));
    await user.selectOptions(screen.getByLabelText("Destination"), "api.semanticscholar.org");
    await waitFor(() =>
      expect(logUrls.at(-1)?.searchParams.get("destination")).toBe("api.semanticscholar.org"),
    );
    expect(logUrls.at(-1)?.searchParams.get("since")).toBeTruthy();
  });

  it("downloads the CSV through fetch with the same filters", async () => {
    logRows = [row({})];
    const create = vi.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<OutboundLogPanel />);
    await screen.findByText("sparsity segmentation");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Decision"), "sent");
    await user.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(csvUrls).toHaveLength(1));
    expect(csvUrls[0]!.searchParams.get("decision")).toBe("sent");
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(create).toHaveBeenCalled();
    click.mockRestore();
  });

  it("renders (content not logged) for every llm row", async () => {
    logRows = [row({ kind: "llm", sent_text: "leaky", original_text: "leaky too", stage: "llm" })];
    render(<OutboundLogPanel />);
    expect(await screen.findByText("(content not logged)")).toBeInTheDocument();
    expect(screen.queryByText("leaky")).toBeNull();
  });

  it("shows an error row with Retry instead of the empty state", async () => {
    logStatus = 500;
    render(<OutboundLogPanel />);
    expect(await screen.findByText("Couldn't load the log.")).toBeInTheDocument();
    expect(screen.queryByText("Nothing matches these filters.")).toBeNull();
    logStatus = 200;
    logRows = [row({})];
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("sparsity segmentation")).toBeInTheDocument();
  });

  it("explains an invalid date filter on 422", async () => {
    logStatus = 422;
    render(<OutboundLogPanel />);
    expect(await screen.findByText("The date filter isn't valid.")).toBeInTheDocument();
  });

  it("shows the footer note", async () => {
    render(<OutboundLogPanel />);
    expect(await screen.findByText(/Network-level egress control/)).toBeInTheDocument();
  });
});

describe("OutboundLogPanel held tab", () => {
  const openHeld = () => {
    render(<OutboundLogPanel initialTab="held" />);
    return userEvent.setup();
  };

  it("lists held searches and shows the count on the tab", async () => {
    heldRows = [held({}), held({ id: "h2", text: "dark-field defects" })];
    openHeld();
    expect(await screen.findByText("wafer defect sparse")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Held/ })).toHaveTextContent("2");
    expect(screen.getByRole("tab", { name: /Held/ })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(useNavStore.getState().heldBadge).toBe(2)); // set by a passive effect
  });

  it("shows an error row with Retry when the held list fails to load", async () => {
    heldStatus = 500;
    const user = openHeld();
    expect(await screen.findByText("Couldn't load the log.")).toBeInTheDocument();
    expect(screen.queryByText("No held searches.")).toBeNull();
    heldStatus = 200;
    heldRows = [held({})];
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("wafer defect sparse")).toBeInTheDocument();
  });

  it("a chat's held search only offers Open in chat to decide, landing on its card", async () => {
    heldRows = [held({ unattended: false, session_id: 77, reason: "May reveal TR-0412" })];
    useChatStore.setState({
      sessions: [{ id: 5, title: "Wafer chat", messages: [], backend_session_id: 77 }],
      activeSessionId: null,
    });
    const user = openHeld();
    const list = await screen.findByRole("list", { name: "Held searches" });
    expect(within(list).getByText("Waiting for your OK")).toBeInTheDocument();
    expect(within(list).getByText("May reveal TR-0412")).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: "Wafer chat" })).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Send" })).toBeNull();
    expect(within(list).queryByRole("button", { name: "Discard" })).toBeNull();
    await user.click(within(list).getByRole("button", { name: "Open in chat to decide" }));
    expect(useChatStore.getState().activeSessionId).toBe(5);
    expect(useNavStore.getState().page).toBe("chat");
    expect(useNavStore.getState().focusHeldId).toBe("h1");
  });

  it("toasts when the chat of a held row is not on this device", async () => {
    heldRows = [held({ unattended: false, session_id: 99 })];
    const user = openHeld();
    await user.click(await screen.findByRole("button", { name: "Open in chat to decide" }));
    expect(toastError).toHaveBeenCalledWith("That chat isn't on this device or was deleted.");
  });

  it("a background held search shows where it came from, with Send and Discard", async () => {
    heldRows = [held({ caller: "trending" })];
    openHeld();
    expect(await screen.findByText("From trending")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
  });

  it("sends a held row and removes it, with a plain toast when unattended", async () => {
    heldRows = [held({})];
    const user = openHeld();
    await user.click(await screen.findByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls).toEqual(["send:h1"]));
    await waitFor(() => expect(screen.queryByText("wafer defect sparse")).toBeNull());
    expect(toastFn).toHaveBeenCalledWith("Sent.");
    expect(useNavStore.getState().heldBadge).toBe(0);
  });

  it("discards a held row and removes every returned sibling", async () => {
    heldRows = [held({}), held({ id: "h2", text: "sibling query" })];
    const user = openHeld();
    await user.click((await screen.findAllByRole("button", { name: "Discard" }))[0]!);
    await waitFor(() => expect(calls).toEqual(["discard:h1"]));
    await waitFor(() => expect(screen.queryByText("sibling query")).toBeNull());
    expect(screen.queryByText("wafer defect sparse")).toBeNull();
    expect(toastFn).toHaveBeenCalledWith("Discarded. Nothing was sent.");
  });

  it("keeps the badge right when two removals overlap", async () => {
    heldRows = [held({}), held({ id: "h2", text: "sibling query" }), held({ id: "h3", text: "third query" })];
    sendResult = { status: 200, body: { status: "sent", held_id: "h3", held_ids: ["h3"], text: "x" } };
    const user = openHeld();
    await screen.findByText("third query");
    // Send h3, then discard h1 (+ sibling h2) before h3's exit finishes.
    await user.click(screen.getAllByRole("button", { name: "Send" })[2]!);
    await user.click(screen.getAllByRole("button", { name: "Discard" })[0]!);
    await waitFor(() => expect(calls).toEqual(["send:h3", "discard:h1"]));
    await waitFor(() => expect(screen.queryByText("sibling query")).toBeNull());
    await waitFor(() => expect(screen.queryByText("third query")).toBeNull());
    expect(useNavStore.getState().heldBadge).toBe(0);
  });

  it("clears its leave timers on unmount", async () => {
    heldRows = [held({}), held({ id: "h2", text: "sibling query" })];
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = openHeld();
    await screen.findByText("sibling query");
    await waitFor(() => expect(useNavStore.getState().heldBadge).toBe(2));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const view = screen.getAllByRole("button", { name: "Discard" })[0]!;
      await user.click(view);
      await waitFor(() => expect(calls).toEqual(["discard:h1"]));
      const pending = vi.getTimerCount();
      expect(pending).toBeGreaterThan(0);
      cleanup();
      expect(vi.getTimerCount()).toBeLessThan(pending);
      vi.runOnlyPendingTimers();
      expect(useNavStore.getState().heldBadge).toBe(2); // nothing ran after unmount
      expect(errors).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
      errors.mockRestore();
    }
  });

  it("explains a held search that nothing can send", async () => {
    heldRows = [held({})];
    sendResult = { status: 409, body: { detail: "no_resender" } };
    const user = openHeld();
    await user.click(await screen.findByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "Nothing can send this search right now. Discard it, or try again later.",
      ),
    );
    expect(screen.getByText("wafer defect sparse")).toBeInTheDocument();
  });

  it("keeps the row and explains a stale 409", async () => {
    heldRows = [held({})];
    sendResult = { status: 409, body: { detail: "not_pending" } };
    const user = openHeld();
    await user.click(await screen.findByRole("button", { name: "Send" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("This search was already handled."));
    expect(screen.getByText("wafer defect sparse")).toBeInTheDocument();
  });
});
