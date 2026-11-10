import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AutoApproveToggle } from "@/components/chat/AutoApproveToggle";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: toastError } }));

const server = setupServer();
const patches: { id: string; body: unknown }[] = [];

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  patches.length = 0;
  toastError.mockReset();
  server.resetHandlers(
    http.patch(`${API_BASE_URL}/sessions/:id`, async ({ request, params }) => {
      const body = (await request.json()) as { auto_approve: boolean };
      patches.push({ id: String(params.id), body });
      return HttpResponse.json({ id: Number(params.id), auto_approve: body.auto_approve });
    }),
  );
  useChatStore.setState({
    sessions: [
      { id: 1, title: "c", messages: [], backend_session_id: 42, auto_approve: false },
    ],
    activeSessionId: 1,
  });
});

const chip = () => screen.getByRole("button", { name: /Approval mode/ });
const pick = (name: RegExp) => screen.findByRole("menuitemradio", { name });

describe("AutoApproveToggle", () => {
  it("shows the current mode and switches to Auto from the menu", async () => {
    const user = userEvent.setup();
    render(<AutoApproveToggle />);
    expect(chip()).toHaveTextContent("Ask first");
    await user.click(chip());
    expect(await pick(/Ask first/)).toHaveAttribute("aria-checked", "true");
    await user.click(await pick(/^Auto/));
    await waitFor(() =>
      expect(patches).toEqual([{ id: "42", body: { auto_approve: true } }]),
    );
    expect(useChatStore.getState().sessions[0]!.auto_approve).toBe(true);
    expect(chip()).toHaveTextContent("Auto");
  });

  it("reflects the session's stored state", async () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "c", messages: [], backend_session_id: 42, auto_approve: true },
      ],
    });
    const user = userEvent.setup();
    render(<AutoApproveToggle />);
    expect(chip()).toHaveTextContent("Auto");
    await user.click(chip());
    expect(await pick(/^Auto/)).toHaveAttribute("aria-checked", "true");
  });

  it("picking the current mode sends nothing", async () => {
    const user = userEvent.setup();
    render(<AutoApproveToggle />);
    await user.click(chip());
    await user.click(await pick(/Ask first/));
    expect(patches).toEqual([]);
  });

  it("reverts and reports a failed save", async () => {
    server.use(
      http.patch(`${API_BASE_URL}/sessions/:id`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    render(<AutoApproveToggle />);
    await user.click(chip());
    await user.click(await pick(/^Auto/));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Couldn't change auto-approve"));
    expect(useChatStore.getState().sessions[0]!.auto_approve).toBe(false);
  });

  it("each mode item has a tooltip with the full explanation", async () => {
    const user = userEvent.setup();
    render(<AutoApproveToggle />);
    await user.click(chip());
    await user.hover(await pick(/^Auto/));
    expect(await screen.findByText(/a quick check decides/)).toBeInTheDocument();
  });
});
