import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmActionCard } from "@/components/chat/ConfirmActionCard";
import { API_BASE_URL } from "@/lib/api";
import type { ConfirmRequestData } from "@/types/domain";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: toastError } }));

const server = setupServer();
const calls: string[] = [];

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  calls.length = 0;
  toastError.mockReset();
  server.resetHandlers(
    http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, ({ params }) => {
      calls.push(`confirm:${String(params.id)}`);
      return HttpResponse.json({
        status: "done",
        result: {
          content: "Removed 'Attention' from this chat.",
          record: { removed: true, paper_content_id: 3, title: "Attention" },
        },
      });
    }),
    http.post(`${API_BASE_URL}/chat/actions/:id/dismiss`, ({ params }) => {
      calls.push(`dismiss:${String(params.id)}`);
      return HttpResponse.json({ status: "dismissed" });
    }),
  );
});

const base: ConfirmRequestData = {
  action_id: "a1",
  tool: "remove_paper",
  args_summary: '"Attention" (paper 3)',
  target: { kind: "paper", id: 3, title: "Attention" },
  reason: "confirmation required",
  reason_kind: "required",
  status: "pending",
};
const SUMMARY = 'Remove "Attention" from this chat';

/** Holds the event data like the chat store does. */
function Harness({
  initial = base,
  onDone,
}: {
  initial?: ConfirmRequestData;
  onDone?: () => void;
}) {
  const [data, setData] = useState(initial);
  return (
    <ConfirmActionCard
      data={data}
      onChange={(patch) => setData((d) => ({ ...d, ...patch }))}
      onDone={onDone}
    />
  );
}

describe("ConfirmActionCard", () => {
  it("names the target in words (not the raw call), enters with fade + zoom", () => {
    render(<Harness />);
    expect(screen.getByText(SUMMARY)).toBeInTheDocument();
    // The backend's English reason is never shown raw.
    expect(screen.queryByText("confirmation required")).toBeNull();
    const panel = screen.getByRole("group", { name: "Confirm this action" });
    expect(panel.className).toContain("animate-in");
    expect(panel.className).toContain("zoom-in-95");
  });

  it("Confirm calls the confirm endpoint, then shows the result", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    render(<Harness onDone={onDone} />);
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(
      await screen.findByText('Removed "Attention" from this chat.'),
    ).toBeInTheDocument();
    expect(calls).toEqual(["confirm:a1"]);
    expect(onDone).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("shows a failed action's error", async () => {
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, () =>
        HttpResponse.json({ status: "failed", error: "paper not attached" }),
      ),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("The action failed.")).toBeInTheDocument();
    expect(screen.getByText("paper not attached")).toBeInTheDocument();
  });

  it("Dismiss collapses the card (data-state=closed) into a muted dismissed line", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() =>
      expect(screen.getByTestId("confirm-card")).toHaveAttribute("data-state", "closed"),
    );
    const card = screen.getByTestId("confirm-card");
    expect(card.className).toContain("data-[state=closed]:grid-rows-[0fr]");
    expect(card.className).toContain("transition-[grid-template-rows,opacity]");
    expect(calls).toEqual(["dismiss:a1"]);
    await waitFor(() => expect(screen.queryByTestId("confirm-card")).toBeNull());
    const line = screen.getByText(`Dismissed: ${SUMMARY}`);
    expect(line.className).toContain("animate-in");
  });

  it("a double click on Confirm sends one request", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, async () => {
        calls.push("confirm");
        await gate;
        return HttpResponse.json({ status: "done", result: { content: "Done once." } });
      }),
    );
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Confirm" });
    fireEvent.click(button);
    fireEvent.click(button);
    release();
    expect(await screen.findByText('Removed "Attention" from this chat.')).toBeInTheDocument();
    expect(calls).toEqual(["confirm"]);
  });

  it("a 409 re-reads the action: confirmed elsewhere shows done", async () => {
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, () =>
        HttpResponse.json({ detail: "action is not pending" }, { status: 409 }),
      ),
      http.get(`${API_BASE_URL}/chat/actions/:id`, () =>
        HttpResponse.json({ status: "done", result: { content: "Removed earlier." } }),
      ),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText('Removed "Attention" from this chat.')).toBeInTheDocument();
  });

  it("a 409 whose action was dismissed shows the dismissed line", async () => {
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/confirm`, () =>
        HttpResponse.json({ detail: "action is not pending" }, { status: 409 }),
      ),
      http.get(`${API_BASE_URL}/chat/actions/:id`, () =>
        HttpResponse.json({ status: "dismissed" }),
      ),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText(`Dismissed: ${SUMMARY}`)).toBeInTheDocument();
  });

  it("a 409 for an unknown action marks the card unavailable", async () => {
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/dismiss`, () =>
        HttpResponse.json({ detail: "action is not pending" }, { status: 409 }),
      ),
      http.get(`${API_BASE_URL}/chat/actions/:id`, () =>
        HttpResponse.json({ detail: "unknown action" }, { status: 404 }),
      ),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(
      await screen.findByText("This action is no longer available here."),
    ).toBeInTheDocument();
    expect(toastError).toHaveBeenCalledWith("This action was already handled.");
  });

  it("replays a done card with its result and a dismissed one as a muted line", () => {
    const { rerender } = render(
      <ConfirmActionCard
        data={{ ...base, status: "done", result: { content: "Removed." } }}
        onChange={() => undefined}
      />,
    );
    expect(screen.getByText('Removed "Attention" from this chat.')).toBeInTheDocument();
    rerender(
      <ConfirmActionCard data={{ ...base, status: "dismissed" }} onChange={() => undefined} />,
    );
    expect(screen.queryByTestId("confirm-card")).toBeNull();
    expect(screen.getByText(`Dismissed: ${SUMMARY}`)).toBeInTheDocument();
  });

  it("a forget card shows the memory's text and scope (review #1/#4)", () => {
    render(
      <Harness
        initial={{
          ...base,
          tool: "forget",
          args_summary: 'memory 12: "always answer in Japanese"',
          target: { kind: "memory", id: 12, content: "always answer in Japanese", scope: "global" },
          reason_kind: "classifier",
        }}
      />,
    );
    expect(
      screen.getByText('Forget this memory (all chats): "always answer in Japanese"'),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Auto-approve wasn't sure this is exactly what you asked for."),
    ).toBeInTheDocument();
  });

  it("a card replayed without a target falls back to the summary text", () => {
    render(<Harness initial={{ ...base, target: undefined, reason_kind: undefined }} />);
    expect(screen.getByText(base.args_summary)).toBeInTheDocument();
  });

  it("a late 409 never overwrites a result the card already shows (carry-forward A)", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.post(`${API_BASE_URL}/chat/actions/:id/dismiss`, () =>
        HttpResponse.json({ detail: "action is not pending" }, { status: 409 }),
      ),
      http.get(`${API_BASE_URL}/chat/actions/:id`, async () => {
        await gate;
        return HttpResponse.json({ detail: "unknown action" }, { status: 404 });
      }),
    );
    const changes: Partial<ConfirmRequestData>[] = [];
    const { rerender } = render(
      <ConfirmActionCard data={base} onChange={(p) => changes.push(p)} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    // Meanwhile the card learned it failed (e.g. a replay).
    rerender(
      <ConfirmActionCard
        data={{ ...base, status: "failed", error: "x" }}
        onChange={(p) => changes.push(p)}
      />,
    );
    release();
    await new Promise((r) => setTimeout(r, 50));
    expect(changes.filter((c) => c.status === "unavailable")).toEqual([]);
  });

  it("a running card polls until the action ends (review #11)", async () => {
    let n = 0;
    server.use(
      http.get(`${API_BASE_URL}/chat/actions/:id`, () => {
        n += 1;
        return n < 2
          ? HttpResponse.json({ status: "running" })
          : HttpResponse.json({
              status: "done",
              result: { content: "x", record: { title: "Attention" } },
            });
      }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<Harness initial={{ ...base, status: "running" }} />);
      await vi.advanceTimersByTimeAsync(2100);
      await vi.advanceTimersByTimeAsync(2100);
      await waitFor(() =>
        expect(screen.getByText('Removed "Attention" from this chat.')).toBeInTheDocument(),
      );
      expect(n).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
