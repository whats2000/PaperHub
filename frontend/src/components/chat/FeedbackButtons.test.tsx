import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { FeedbackButtons } from "@/components/chat/FeedbackButtons";
import { ChatThread } from "@/components/chat/ChatThread";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { API_BASE_URL } from "@/lib/api";
import { useFeedbackStore } from "@/store/feedback";
import type { FeedbackPut } from "@/types/feedback";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: toastSuccess, error: toastError } }));

const server = setupServer();
let puts: FeedbackPut[] = [];

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  puts = [];
  toastSuccess.mockReset();
  toastError.mockReset();
  useFeedbackStore.setState({ byRun: {} });
  server.resetHandlers(
    http.put(`${API_BASE_URL}/feedback/runs/:runId`, async ({ request }) => {
      puts.push((await request.json()) as FeedbackPut);
      return HttpResponse.json({ ok: true });
    }),
    http.get(`${API_BASE_URL}/feedback/runs/:runId/guess`, () =>
      HttpResponse.json({ reasons: ["citation"] }),
    ),
    http.get(`${API_BASE_URL}/feedback/presets`, () =>
      HttpResponse.json([{ label: "Too long", stage: "answer" }]),
    ),
  );
});

const upButton = () => screen.getByRole("button", { name: "Good answer" });
const downButton = () => screen.getByRole("button", { name: "Bad answer" });
const chip = (name: string | RegExp) => screen.getByRole("button", { name });

describe("FeedbackButtons", () => {
  it("thumbs up sends polarity up, and a second click clears it", async () => {
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);

    await user.click(upButton());
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ polarity: "up", reasons: [] });
    expect(upButton()).toHaveAttribute("aria-pressed", "true");

    await user.click(upButton());
    await waitFor(() => expect(puts).toHaveLength(2));
    expect(puts[1]).toEqual({ polarity: null, reasons: [] });
    expect(upButton()).toHaveAttribute("aria-pressed", "false");
  });

  it("thumbs down pre-selects the guessed chips and tags them", async () => {
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);

    await user.click(downButton());
    await waitFor(() =>
      expect(chip(/Wrong citation/)).toHaveAttribute("aria-pressed", "true"),
    );
    expect(chip(/Wrong citation/)).toHaveTextContent("system guess");
    expect(chip("Factual error")).toHaveAttribute("aria-pressed", "false");
    // Promoted presets show their label as-is.
    expect(await screen.findByRole("button", { name: "Too long" })).toBeInTheDocument();
  });

  it("choosing Other reason reveals the textarea", async () => {
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);

    await user.click(downButton());
    const other = await screen.findByPlaceholderText("Describe the other reason…");
    expect(other.closest("[data-state]")).toHaveAttribute("data-state", "closed");

    await user.click(chip("Other reason"));
    expect(other.closest("[data-state]")).toHaveAttribute("data-state", "open");
  });

  it("submit sends reasons and text, then the popover exits", async () => {
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);

    await user.click(downButton());
    await waitFor(() =>
      expect(chip(/Wrong citation/)).toHaveAttribute("aria-pressed", "true"),
    );
    await user.click(chip("Factual error"));
    await user.click(chip("Other reason"));
    await user.type(
      screen.getByPlaceholderText("Describe the other reason…"),
      "missed table 2",
    );
    await user.click(screen.getByRole("button", { name: "Correct answer (optional)" }));
    await user.type(screen.getByPlaceholderText("The right answer would be…"), "42%");

    const title = screen.getByText("What went wrong?");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await user.click(screen.getByRole("button", { name: "Submit" }));
      await waitFor(() => expect(puts).toHaveLength(1));
      expect(puts[0]).toEqual({
        polarity: "down",
        reasons: ["citation", "factual", "other"],
        other_text: "missed table 2",
        correction: "42%",
      });
      await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
      // Still mounted while the exit animation plays...
      expect(title).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200);
      });
      // ...then removed.
      await waitFor(() => expect(title).not.toBeInTheDocument());
    } finally {
      vi.useRealTimers();
    }
    expect(downButton()).toHaveAttribute("aria-pressed", "true");
  });

  it("restores the pressed thumb from the saved session feedback", async () => {
    server.use(
      http.get(`${API_BASE_URL}/feedback/runs`, ({ request }) => {
        expect(new URL(request.url).searchParams.get("session_id")).toBe("7");
        return HttpResponse.json([
          { run_id: 42, polarity: "down", reasons: ["factual"] },
          { run_id: 43, polarity: "up", reasons: [] },
        ]);
      }),
    );
    await useFeedbackStore.getState().hydrate(7);
    render(
      <>
        <FeedbackButtons runId={42} />
        <FeedbackButtons runId={43} />
      </>,
    );
    const [down42, down43] = screen.getAllByRole("button", { name: "Bad answer" });
    const [up42, up43] = screen.getAllByRole("button", { name: "Good answer" });
    expect(down42).toHaveAttribute("aria-pressed", "true");
    expect(up42).toHaveAttribute("aria-pressed", "false");
    expect(up43).toHaveAttribute("aria-pressed", "true");
    expect(down43).toHaveAttribute("aria-pressed", "false");
  });

  it("reverts and toasts when saving fails", async () => {
    server.use(
      http.put(`${API_BASE_URL}/feedback/runs/:runId`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);
    await user.click(upButton());
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(upButton()).toHaveAttribute("aria-pressed", "false");
  });
});

describe("FeedbackButtons pressed state", () => {
  it("draws a pressed thumb solid and an unpressed one as a muted outline", () => {
    useFeedbackStore.setState({
      byRun: { 42: { polarity: "up", reasons: [], otherText: null, correction: null } },
    });
    render(<FeedbackButtons runId={42} />);
    for (const btn of [upButton(), downButton()]) {
      expect(btn.className).toContain("text-muted-foreground");
      expect(btn.className).toContain("aria-pressed:[&_svg]:fill-current");
      expect(btn.className).not.toContain("fill-opacity");
    }
    expect(upButton()).toHaveAttribute("title", "Marked as a good answer · click to remove");
    expect(downButton()).toHaveAttribute("title", "Bad answer");
  });

  it("a pressed thumbs down is removed on one click, without opening the reasons", async () => {
    useFeedbackStore.setState({
      byRun: { 42: { polarity: "down", reasons: ["factual"], otherText: null, correction: null } },
    });
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);
    expect(downButton()).toHaveAttribute("title", "Marked as a bad answer · click to remove");
    await user.click(downButton());
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ polarity: null, reasons: [] });
    expect(downButton()).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "Submit" })).not.toBeInTheDocument();
    expect(toastSuccess).toHaveBeenCalledWith("Feedback removed");
  });

  it("removing a thumbs up says so too", async () => {
    useFeedbackStore.setState({
      byRun: { 42: { polarity: "up", reasons: [], otherText: null, correction: null } },
    });
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);
    await user.click(upButton());
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(toastSuccess).toHaveBeenCalledWith("Feedback removed");
  });
});

describe("MessageBubble thumbs placement", () => {
  it("shows thumbs on a completed answer with a run id", () => {
    render(
      <MessageBubble message={{ role: "assistant", content: "a", run_id: 9, status: "ok" }} />,
    );
    expect(screen.getByRole("button", { name: "Good answer" })).toBeInTheDocument();
  });

  it("hides thumbs while streaming, without a run id, and on user messages", () => {
    render(
      <>
        <MessageBubble
          message={{ role: "assistant", content: "a", run_id: 9, status: "streaming" }}
        />
        <MessageBubble message={{ role: "assistant", content: "a", run_id: null, status: "ok" }} />
        <MessageBubble message={{ role: "user", content: "q", run_id: 9 }} />
      </>,
    );
    expect(screen.queryByRole("button", { name: "Good answer" })).not.toBeInTheDocument();
  });
});

describe("FeedbackButtons fix round 1", () => {
  it("a quick submit before presets load keeps a guessed preset reason", async () => {
    server.use(
      http.get(`${API_BASE_URL}/feedback/presets`, async () => {
        await delay("infinite");
        return HttpResponse.json([]);
      }),
      http.get(`${API_BASE_URL}/feedback/runs/:runId/guess`, () => HttpResponse.json({ reasons: ["Too long"] })),
    );
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);
    await user.click(downButton());
    const submit = await screen.findByRole("button", { name: "Submit" });
    await waitFor(() => expect(screen.getByRole("button", { name: /Wrong citation/ })).toBeInTheDocument());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20)); // let the guess land
    });
    await user.click(submit);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]?.reasons).toEqual(["Too long"]);
  });

  it("a late hydrate response does not overwrite a thumb clicked meanwhile", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.get(`${API_BASE_URL}/feedback/runs`, async () => {
        await gate;
        return HttpResponse.json([{ run_id: 42, polarity: "down", reasons: ["factual"] }]);
      }),
    );
    const user = userEvent.setup();
    render(<FeedbackButtons runId={42} />);
    const hydrating = useFeedbackStore.getState().hydrate(7);
    await user.click(upButton());
    await waitFor(() => expect(puts).toHaveLength(1));
    release();
    await act(async () => {
      await hydrating;
    });
    expect(upButton()).toHaveAttribute("aria-pressed", "true");
    expect(downButton()).toHaveAttribute("aria-pressed", "false");
  });
});

describe("ChatThread feedback hydration", () => {
  it("loads the session's feedback when the active session changes", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const asked: string[] = [];
    server.use(
      http.get(`${API_BASE_URL}/feedback/runs`, ({ request }) => {
        const sid = new URL(request.url).searchParams.get("session_id") ?? "";
        asked.push(sid);
        return HttpResponse.json(
          sid === "11" ? [{ run_id: 5, polarity: "up", reasons: [] }] : [],
        );
      }),
    );
    const answer = { role: "assistant" as const, content: "a", run_id: 5, status: "ok" as const };
    const { rerender } = render(
      <ChatThread session={{ id: 1, title: "a", messages: [], backend_session_id: 10 }} />,
    );
    await waitFor(() => expect(asked).toEqual(["10"]));
    rerender(
      <ChatThread session={{ id: 2, title: "b", messages: [answer], backend_session_id: 11 }} />,
    );
    await waitFor(() => expect(asked).toEqual(["10", "11"]));
    await waitFor(() => expect(upButton()).toHaveAttribute("aria-pressed", "true"));
  });
});
