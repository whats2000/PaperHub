import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchApprovalCard } from "@/components/chat/SearchApprovalCard";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import type { ConfirmRequestData } from "@/types/domain";

const { toastError, toastInfo } = vi.hoisted(() => ({ toastError: vi.fn(), toastInfo: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastInfo, { success: vi.fn(), error: toastError }),
}));

const server = setupServer();
const calls: { path: string; body: unknown }[] = [];
let alsoAllowed: { action_id: string; text: string }[] = [];
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  calls.length = 0;
  alsoAllowed = [];
  toastError.mockReset();
  toastInfo.mockReset();
  useChatStore.getState().reset();
  server.resetHandlers(
    http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, async ({ params, request }) => {
      const body = (await request.json()) as { text?: string; allow_chat?: boolean };
      calls.push({ path: `confirm:${String(params.id)}`, body });
      const text = body.text ?? base.args_summary;
      return HttpResponse.json({
        status: "allowed",
        text,
        edited: body.text !== undefined,
        allow_chat: body.allow_chat === true,
        resumed: true,
        also_allowed: body.allow_chat === true ? alsoAllowed : [],
      });
    }),
    http.post(`${API_BASE_URL}/chat/actions/:id/dismiss`, async ({ params, request }) => {
      calls.push({ path: `dismiss:${String(params.id)}`, body: await request.json() });
      return HttpResponse.json({ status: "dismissed", resumed: true });
    }),
  );
});

const base: ConfirmRequestData = {
  action_id: "s1",
  tool: "search_papers",
  args_summary: "sparsity prior segmentation of very small defects",
  target: {
    kind: "search",
    query: "sparsity prior segmentation of very small defects",
    destinations: ["public-web-search", "api.semanticscholar.org"],
  },
  reason: "Ask first is on for this chat",
  reason_kind: "ask_first",
  status: "pending",
};

/** The first "Don't allow" opens the note box; the box has its own. */
const denyToggle = () => screen.getAllByRole("button", { name: "Don't allow" })[0]!;

function Harness({ initial = base }: { initial?: ConfirmRequestData }) {
  const [data, setData] = useState(initial);
  return <SearchApprovalCard data={data} onChange={(p) => setData((d) => ({ ...d, ...p }))} />;
}

describe("SearchApprovalCard", () => {
  it("shows the query, where it goes and why, with the four choices", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Allow this search?" })).toHaveAttribute(
      "data-action-id",
      "s1",
    );
    expect(screen.getByRole("textbox", { name: "Search query" })).toHaveValue(base.args_summary);
    expect(screen.getByText(/public web search/)).toBeInTheDocument();
    expect(screen.getByText(/Semantic Scholar/)).toBeInTheDocument();
    expect(
      screen.getByText("Ask first is on for this chat. Nothing has been sent."),
    ).toBeInTheDocument();
    expect(screen.getByText(/follow-up queries/)).toBeInTheDocument();
    for (const name of ["Allow", "Allow searches in this chat", "Edit, then allow"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(denyToggle()).toHaveAttribute("aria-expanded", "false");
    // The note box is folded away until "Don't allow".
    expect(screen.getByTestId("ask-deny")).toHaveAttribute("data-state", "closed");
  });

  it("Allow hands the query to the paused turn and collapses into a line", async () => {
    render(<Harness />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Allow" }));
    await waitFor(() =>
      expect(screen.getByTestId("ask-card")).toHaveAttribute("data-state", "closed"),
    );
    expect(calls).toEqual([{ path: "confirm:s1", body: { allow_chat: false } }]);
    const line = await screen.findByTestId("ask-line");
    expect(line).toHaveAttribute("data-state", "allowed");
    expect(line).toHaveTextContent(`Allowed: ${base.args_summary}`);
  });

  it("Allow searches in this chat sets the chat's flag and says actions still ask", async () => {
    const id = useChatStore.getState().newSession();
    render(<Harness />);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Allow searches in this chat" }));
    const line = await screen.findByTestId("ask-line");
    expect(line).toHaveTextContent("later searches in this chat won't ask");
    expect(calls[0]!.body).toEqual({ allow_chat: true });
    expect(useChatStore.getState().sessions.find((s) => s.id === id)?.allow_searches).toBe(true);
    expect(toastInfo).toHaveBeenCalledWith(
      "Searches in this chat now run without asking. Actions still ask.",
    );
  });

  it("with other searches waiting, the chat-wide allow says it allows them too", async () => {
    alsoAllowed = [
      { action_id: "s2", text: "wafer maps" },
      { action_id: "s3", text: "dark-field inspection" },
    ];
    const onAlsoAllowed = vi.fn();
    render(
      <SearchApprovalCard
        data={base}
        onChange={() => undefined}
        waitingSiblings={2}
        onAlsoAllowed={onAlsoAllowed}
      />,
    );
    const button = screen.getByRole("button", {
      name: "Allow searches in this chat · also allows 2 waiting",
    });
    await userEvent.setup().click(button);
    await waitFor(() => expect(onAlsoAllowed).toHaveBeenCalledWith(alsoAllowed));
  });

  it("a card allowed by the chat setting settles into its own line", async () => {
    function Sibling() {
      const [data, setData] = useState(base);
      return (
        <>
          <button
            type="button"
            onClick={() =>
              setData((d) => ({
                ...d,
                status: "running",
                result: {
                  decision: "allowed",
                  text: base.args_summary,
                  allow_chat: true,
                  by_chat_setting: true,
                },
              }))
            }
          >
            settle
          </button>
          <SearchApprovalCard data={data} onChange={(p) => setData((d) => ({ ...d, ...p }))} />
        </>
      );
    }
    render(<Sibling />);
    await userEvent.setup().click(screen.getByRole("button", { name: "settle" }));
    // It collapses like a card you decided, then shows why it was allowed.
    expect(screen.getByTestId("ask-card")).toHaveAttribute("data-state", "closed");
    const line = await screen.findByTestId("ask-line");
    expect(line).toHaveTextContent(
      `Allowed: ${base.args_summary} · searches are allowed in this chat`,
    );
  });

  it("Edit, then allow sends the edited query; a sensitive term keeps the card", async () => {
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, async ({ request }) => {
        const body = (await request.json()) as { text?: string };
        if (body.text?.includes("KESTREL")) {
          return HttpResponse.json({ detail: "sensitive_term", term: "KESTREL" }, { status: 422 });
        }
        calls.push({ path: "confirm", body });
        return HttpResponse.json({
          status: "allowed",
          text: body.text,
          edited: true,
          allow_chat: false,
          resumed: true,
        });
      }),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Edit, then allow" }));
    const box = screen.getByRole("textbox", { name: "Search query" });
    await user.clear(box);
    await user.type(box, "KESTREL defects");
    await user.click(screen.getByRole("button", { name: "Allow edited search" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("sensitive term (KESTREL)");
    expect(screen.getByTestId("ask-card")).toHaveAttribute("data-state", "open");
    await user.clear(box);
    await user.type(box, "small defect sparsity");
    await user.click(screen.getByRole("button", { name: "Allow edited search" }));
    const line = await screen.findByTestId("ask-line");
    expect(line).toHaveTextContent("Allowed after your edit: small defect sparsity");
    expect(calls).toEqual([
      { path: "confirm", body: { text: "small defect sparsity", allow_chat: false } },
    ]);
  });

  it("Don't allow opens the note box and passes the note", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(denyToggle());
    const box = screen.getByTestId("ask-deny");
    expect(box).toHaveAttribute("data-state", "open");
    await user.type(
      screen.getByLabelText("Tell the agent what to do instead (optional)"),
      "Only use papers already in this chat.",
    );
    // The box's own "Don't allow" confirms; the first one toggles the box.
    const confirms = screen.getAllByRole("button", { name: "Don't allow" });
    await user.click(confirms[confirms.length - 1]!);
    const line = await screen.findByTestId("ask-line");
    expect(line).toHaveAttribute("data-state", "declined");
    expect(line).toHaveTextContent(
      "Not searched. You told the agent: “Only use papers already in this chat.”",
    );
    expect(calls).toEqual([
      { path: "dismiss:s1", body: { note: "Only use papers already in this chat." } },
    ]);
  });

  it("Cancel folds the note box back", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(denyToggle());
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByTestId("ask-deny")).toHaveAttribute("data-state", "closed");
    expect(calls).toEqual([]);
  });

  it("a card whose turn ended says nothing was searched", async () => {
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, () =>
        HttpResponse.json({ detail: "turn_ended" }, { status: 409 }),
      ),
    );
    render(<Harness />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Allow" }));
    expect(await screen.findByTestId("ask-line")).toHaveTextContent(
      "Not searched. This turn ended before you decided.",
    );
  });

  it.each([
    [{ status: "done", result: { decision: "allowed", text: "q", edited: false } }, "Allowed: q"],
    [
      { status: "dismissed", result: { decision: "declined", note: null } },
      "Not searched. You didn't allow this search.",
    ],
    [{ status: "dismissed", result: null }, "Not searched. The turn was stopped."],
    [{ status: "unavailable" }, "This search is no longer available."],
  ] as const)("a replayed card shows its decision (%#)", (patch, text) => {
    render(<Harness initial={{ ...base, ...(patch as Partial<ConfirmRequestData>) }} />);
    expect(screen.getByTestId("ask-line")).toHaveTextContent(text);
  });
});
