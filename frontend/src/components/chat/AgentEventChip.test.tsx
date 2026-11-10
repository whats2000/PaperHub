import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentEventChip, type ChipEvent } from "@/components/chat/AgentEventChip";
import { API_BASE_URL } from "@/lib/api";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const server = setupServer();
const calls: { method: string; path: string; body: unknown }[] = [];

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  calls.length = 0;
  server.resetHandlers(
    http.patch(`${API_BASE_URL}/papers/:id`, async ({ request, params }) => {
      calls.push({
        method: "PATCH",
        path: `/papers/${String(params.id)}`,
        body: await request.json(),
      });
      return HttpResponse.json({ enabled: false });
    }),
    http.delete(`${API_BASE_URL}/memories/:id`, ({ params }) => {
      calls.push({ method: "DELETE", path: `/memories/${String(params.id)}`, body: null });
      return new HttpResponse(null, { status: 204 });
    }),
    http.get(`${API_BASE_URL}/memories`, () => HttpResponse.json([])),
  );
});

function Harness({ initial }: { initial: ChipEvent }) {
  const [ev, setEv] = useState(initial);
  return (
    <AgentEventChip
      ev={ev}
      sessionId={1}
      onChange={(patch) => setEv((e) => ({ ...e, data: { ...e.data, ...patch } }) as ChipEvent)}
    />
  );
}

describe("AgentEventChip", () => {
  it("paper chip: Undo disables the reference", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        initial={{
          event: "paper_added",
          data: { papers_id: 7, paper_content_id: 3, title: "Attention Is All You Need" },
        }}
      />,
    );
    expect(screen.getByText("Added paper")).toBeInTheDocument();
    expect(screen.getByText("Attention Is All You Need")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(calls).toEqual([{ method: "PATCH", path: "/papers/7", body: { enabled: false } }]),
    );
    expect(await screen.findByText("Undone")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("memory chip: Undo deletes the memory", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        initial={{
          event: "memory_saved",
          data: { memory_id: 5, content: "Answer in Japanese", scope: "global" },
        }}
      />,
    );
    expect(screen.getByText("Saved to memory")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls[0]).toEqual({ method: "DELETE", path: "/memories/5", body: null }));
    expect(await screen.findByText("Undone")).toBeInTheDocument();
  });

  it("a replayed chip that can no longer be undone has no Undo", () => {
    render(
      <Harness
        initial={{
          event: "paper_added",
          data: { papers_id: 2, paper_content_id: 1, title: "T", undoable: false },
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });
});
