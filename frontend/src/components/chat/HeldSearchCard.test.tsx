import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { HeldSearchCard } from "@/components/chat/HeldSearchCard";
import { API_BASE_URL } from "@/lib/api";
import { useNavStore } from "@/store/nav";
import type { OutboundHeldData } from "@/types/domain";

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
    http.post(`${API_BASE_URL}/outbound/held/:id/send`, async ({ params, request }) => {
      const body = (await request.json()) as { text?: string };
      calls.push(`send:${String(params.id)}:${body.text ?? "-"}`);
      return HttpResponse.json({
        status: "sent",
        held_id: params.id,
        held_ids: [params.id],
        text: body.text ?? "dark-field micro-defect sparsity",
      });
    }),
    http.post(`${API_BASE_URL}/outbound/held/:id/discard`, ({ params }) => {
      calls.push(`discard:${String(params.id)}`);
      return HttpResponse.json({ status: "discarded", held_id: params.id, held_ids: [params.id] });
    }),
  );
});

const base: OutboundHeldData = {
  held_id: "h1",
  held_ids: ["h1"],
  text: "dark-field micro-defect sparsity",
  reason: "it may reveal the inspection setup",
  destination: "public-web-search",
  status: "pending",
};

function Harness({
  initial = base,
  ran = false,
  onRun = vi.fn().mockResolvedValue(undefined),
}: {
  initial?: OutboundHeldData;
  ran?: boolean;
  onRun?: (id: string, text: string) => Promise<void>;
}) {
  const [data, setData] = useState(initial);
  return (
    <HeldSearchCard
      data={data}
      followUpRan={ran}
      onChange={(p) => setData((d) => ({ ...d, ...p }))}
      onRun={onRun}
    />
  );
}

describe("HeldSearchCard", () => {
  it("a send the paused turn takes (resumed) starts no follow-up turn", async () => {
    server.use(
      http.post(`${API_BASE_URL}/outbound/held/:id/send`, ({ params }) =>
        HttpResponse.json({
          status: "sent", held_id: params.id, held_ids: [params.id],
          text: base.text, resumed: true,
        }),
      ),
    );
    const onRun = vi.fn().mockResolvedValue(undefined);
    render(<Harness onRun={onRun} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Send as shown" }));
    const line = await screen.findByTestId("held-line");
    expect(line).toHaveAttribute("data-state", "sent");
    expect(onRun).not.toHaveBeenCalled();
  });

  it("shows the exact text, why and where, with the three buttons", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "This search needs your OK" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Search text" })).toHaveValue(base.text);
    expect(screen.getByText("it may reveal the inspection setup")).toBeInTheDocument();
    expect(screen.getByText("public web search")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send as shown" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit, then send" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Don't send" })).toBeInTheDocument();
  });

  it("Send posts the held send, collapses, then runs the follow-up", async () => {
    const user = userEvent.setup();
    const onRun = vi.fn().mockResolvedValue(undefined);
    render(<Harness onRun={onRun} ran />);
    await user.click(screen.getByRole("button", { name: "Send as shown" }));
    await waitFor(() =>
      expect(screen.getByTestId("held-card")).toHaveAttribute("data-state", "closed"),
    );
    expect(screen.getByTestId("held-card").className).toContain(
      "data-[state=closed]:grid-rows-[0fr]",
    );
    expect(calls).toEqual(["send:h1:-"]);
    expect(onRun).toHaveBeenCalledWith("h1", "dark-field micro-defect sparsity");
    await waitFor(() => expect(screen.queryByTestId("held-card")).toBeNull());
    expect(screen.getByTestId("held-line").textContent).toBe(
      "Sent: dark-field micro-defect sparsity",
    );
  });

  it("Edit sends the edited text and says so", async () => {
    const user = userEvent.setup();
    const onRun = vi.fn().mockResolvedValue(undefined);
    render(<Harness onRun={onRun} ran />);
    await user.click(screen.getByRole("button", { name: "Edit, then send" }));
    expect(screen.getByRole("button", { name: "Send edited text" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel edit" })).toBeInTheDocument();
    const box = screen.getByRole("textbox", { name: "Search text" });
    await user.clear(box);
    await user.type(box, "sparsity prior 2025");
    await user.click(screen.getByRole("button", { name: "Send edited text" }));
    await waitFor(() => expect(screen.getByTestId("held-line")).toBeInTheDocument());
    expect(calls).toEqual(["send:h1:sparsity prior 2025"]);
    expect(onRun).toHaveBeenCalledWith("h1", "sparsity prior 2025");
    expect(screen.getByTestId("held-line").textContent).toBe(
      "Sent after your edit: sparsity prior 2025",
    );
  });

  it("Cancel edit restores the original text", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Edit, then send" }));
    await user.type(screen.getByRole("textbox", { name: "Search text" }), " extra");
    await user.click(screen.getByRole("button", { name: "Cancel edit" }));
    expect(screen.getByRole("textbox", { name: "Search text" })).toHaveValue(base.text);
    expect(screen.getByRole("button", { name: "Send as shown" })).toBeInTheDocument();
  });

  it("a sensitive term in the edit shows an inline error and keeps editing", async () => {
    server.use(
      http.post(`${API_BASE_URL}/outbound/held/:id/send`, () =>
        HttpResponse.json({ detail: "sensitive_term", term: "KESTREL" }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    const onRun = vi.fn();
    render(<Harness onRun={onRun} />);
    await user.click(screen.getByRole("button", { name: "Edit, then send" }));
    await user.type(screen.getByRole("textbox", { name: "Search text" }), " KESTREL");
    await user.click(screen.getByRole("button", { name: "Send edited text" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("KESTREL");
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send edited text" })).toBeEnabled();
    expect(screen.getByTestId("held-card")).toHaveAttribute("data-state", "open");
  });

  it("Don't send collapses into the not-sent line", async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    render(<Harness onRun={onRun} />);
    await user.click(screen.getByRole("button", { name: "Don't send" }));
    await waitFor(() => expect(screen.queryByTestId("held-card")).toBeNull());
    expect(calls).toEqual(["discard:h1"]);
    expect(screen.getByTestId("held-line").textContent).toBe(
      "Not sent. You chose not to send this search.",
    );
    expect(onRun).not.toHaveBeenCalled();
  });

  it("a failed discard says the discard failed and keeps the card", async () => {
    server.use(
      http.post(`${API_BASE_URL}/outbound/held/:id/discard`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Don't send" }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError).toHaveBeenCalledWith("Couldn't discard it. Try again.");
    expect(screen.getByTestId("held-card")).toBeInTheDocument();
  });

  it("409 on send toasts and shows the real state", async () => {
    server.use(
      http.post(`${API_BASE_URL}/outbound/held/:id/send`, () =>
        HttpResponse.json({ detail: "held request is discarded" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Send as shown" }));
    await waitFor(() => expect(screen.getByTestId("held-line")).toBeInTheDocument());
    expect(toastError).toHaveBeenCalledWith("This search was already handled.");
    expect(screen.getByTestId("held-line")).toHaveAttribute("data-state", "discarded");
  });

  it("stores the decision at once, even if the card unmounts mid-collapse", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { unmount } = render(
      <HeldSearchCard
        data={base}
        followUpRan
        onChange={onChange}
        onRun={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Don't send" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ status: "discarded" }));
    unmount();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("keeps the collapsing card until the exit ends, with the store already patched", async () => {
    const user = userEvent.setup();
    const seen: string[] = [];
    function Probe() {
      const [data, setData] = useState(base);
      seen.push(data.status);
      return (
        <HeldSearchCard
          data={data}
          followUpRan
          onChange={(p) => setData((d) => ({ ...d, ...p }))}
          onRun={vi.fn().mockResolvedValue(undefined)}
        />
      );
    }
    render(<Probe />);
    await user.click(screen.getByRole("button", { name: "Send as shown" }));
    await waitFor(() => expect(seen).toContain("sent"));
    // Stored as sent, yet the card is still the collapsing one, not the line.
    expect(screen.getByTestId("held-card")).toHaveAttribute("data-state", "closed");
    expect(screen.queryByTestId("held-line")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("held-line")).toBeInTheDocument());
    expect(screen.queryByTestId("held-card")).toBeNull();
  });

  it("replays final states without buttons", () => {
    const { unmount } = render(<Harness initial={{ ...base, status: "discarded" }} />);
    expect(screen.getByTestId("held-line").textContent).toContain("Not sent.");
    unmount();
    render(<Harness initial={{ ...base, status: "sent" }} ran />);
    expect(screen.getByTestId("held-line").textContent).toBe(`Sent: ${base.text}`);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("R44: sent but never run offers Run the search; a 409 settles on Sent", async () => {
    const user = userEvent.setup();
    const onRun = vi.fn().mockRejectedValue(new Error("POST /chat failed: 409 Conflict"));
    render(<Harness initial={{ ...base, status: "sent" }} onRun={onRun} />);
    expect(screen.getByText(/Approved, not run yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run the search" }));
    expect(onRun).toHaveBeenCalledWith("h1", base.text);
    await waitFor(() =>
      expect(screen.getByTestId("held-line")).toHaveAttribute("data-state", "sent"),
    );
    expect(toastError).toHaveBeenCalledWith("This search was already run.");
    expect(screen.queryByRole("button", { name: "Run the search" })).toBeNull();
  });
});

describe("HeldSearchCard and the held counts", () => {
  beforeEach(() => useNavStore.setState({ heldRefreshSeq: 0, focusHeldId: null }));

  it("asks for a held-count refresh once sent or discarded", async () => {
    const { unmount } = render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Send as shown" }));
    await waitFor(() => expect(useNavStore.getState().heldRefreshSeq).toBe(1));
    unmount();
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Don't send" }));
    await waitFor(() => expect(useNavStore.getState().heldRefreshSeq).toBe(2));
  });

  it("scrolls to, rings and focuses the card a jump points at", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    useNavStore.setState({ focusHeldId: "h1" });
    render(<Harness />);
    await waitFor(() =>
      expect(scroll).toHaveBeenCalledWith({ block: "center", behavior: "smooth" }),
    );
    expect(useNavStore.getState().focusHeldId).toBeNull();
    const group = screen.getByRole("group", { name: "This search needs your OK" });
    expect(group).toHaveAttribute("data-flash", "true");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send as shown" })).toHaveFocus(),
    );
  });

  it("ignores a jump aimed at another card", () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    useNavStore.setState({ focusHeldId: "other" });
    render(<Harness />);
    expect(scroll).not.toHaveBeenCalled();
    expect(useNavStore.getState().focusHeldId).toBe("other");
  });
});
