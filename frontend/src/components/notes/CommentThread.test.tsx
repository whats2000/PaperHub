import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { runThreadAction } from "@/components/notes/commentActions";
import { CommentThread } from "@/components/notes/CommentThread";
import type { NoteComment } from "@/types/notes";

const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastFn, { error: vi.fn() }) }));

const thread: NoteComment = {
  id: "c_1",
  note_id: 7,
  block_id: "p1",
  quote: "attention",
  status: "open",
  created_at: "2026-11-16T10:00:00Z",
  items: [{ id: 1, text: "why this?", created_at: "2026-11-16T10:00:00Z" }],
};

function card(over: Partial<React.ComponentProps<typeof CommentThread>> = {}) {
  const props = {
    thread: null,
    quote: "attention",
    onPost: vi.fn(),
    onResolve: vi.fn(),
    onDelete: vi.fn(),
    onClose: vi.fn(),
    onDiscard: vi.fn(),
    ...over,
  };
  render(<CommentThread {...props} />);
  return props;
}

beforeEach(() => toastFn.mockReset());

describe("the comment box", () => {
  it("posts on Enter and clears the box", async () => {
    const p = card();
    const box = screen.getByRole("textbox");
    await userEvent.type(box, "hello");
    await userEvent.keyboard("{Enter}");
    expect(p.onPost).toHaveBeenCalledWith("hello");
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("keeps a new line on Shift+Enter instead of posting", async () => {
    const p = card();
    const box = screen.getByRole("textbox");
    await userEvent.type(box, "one{Shift>}{Enter}{/Shift}two");
    expect(p.onPost).not.toHaveBeenCalled();
    expect(box).toHaveValue("one\ntwo");
  });

  it("does not post an empty comment", async () => {
    const p = card();
    await userEvent.type(screen.getByRole("textbox"), "   {Enter}");
    expect(p.onPost).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Comment (Enter)" })).toBeDisabled();
  });

  it("drops an empty new comment on Esc, but only closes one with words in it", async () => {
    const p = card();
    const box = screen.getByRole("textbox");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(p.onDiscard).toHaveBeenCalledTimes(1);
    await userEvent.type(box, "half");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(p.onDiscard).toHaveBeenCalledTimes(1);
    expect(p.onClose).toHaveBeenCalledTimes(1);
  });

  it("shows the replies, and Resolve / Delete only on a thread that has some", () => {
    card();
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
    expect(screen.getByText("New comment")).toBeInTheDocument();
    cleanupCard();
    const p = card({ thread });
    expect(screen.getByText("why this?")).toBeInTheDocument();
    expect(screen.getByText("1 comment")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete the thread" }));
    expect(p.onResolve).toHaveBeenCalled();
    expect(p.onDelete).toHaveBeenCalled();
  });
});

function cleanupCard() {
  document.body.innerHTML = "";
}

const t = (_key: string, en: string) => en;

interface ToastOpts {
  action: { label: string; onClick: () => void };
}
const toasts = () => toastFn.mock.calls as [string, ToastOpts][];

/** The last toast shown: its text and its action button. */
function lastAction() {
  const [text, opts] = toasts().at(-1)!;
  return { text, ...opts.action };
}

describe("Resolve and Delete with Undo", () => {
  it("Resolve → Undo → Resolve again", async () => {
    const strip = vi.fn();
    const restore = vi.fn();
    const setStatus = vi.fn().mockResolvedValue(undefined);
    runThreadAction({ kind: "resolve", strip, restore, setStatus, t });

    expect(strip).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(setStatus).toHaveBeenLastCalledWith("resolved"));
    expect(lastAction()).toMatchObject({ text: "Comment resolved", label: "Undo" });

    act(() => lastAction().onClick());
    expect(restore).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(setStatus).toHaveBeenLastCalledWith("open"));
    expect(lastAction()).toMatchObject({ text: "Comment restored", label: "Resolve again" });

    act(() => lastAction().onClick());
    expect(strip).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(setStatus).toHaveBeenLastCalledWith("resolved"));
    expect(lastAction()).toMatchObject({ text: "Comment resolved", label: "Undo" });
  });

  it("Delete goes to the server at once; Undo reopens it and puts the words back", async () => {
    const strip = vi.fn();
    const restore = vi.fn();
    const setStatus = vi.fn().mockResolvedValue(undefined);
    runThreadAction({ kind: "delete", strip, restore, setStatus, t });
    await waitFor(() => expect(setStatus).toHaveBeenLastCalledWith("deleted"));
    expect(lastAction()).toMatchObject({ text: "Comment deleted", label: "Undo" });

    act(() => lastAction().onClick());
    await waitFor(() => expect(setStatus).toHaveBeenLastCalledWith("open"));
    expect(restore).toHaveBeenCalledTimes(1);
    expect(lastAction()).toMatchObject({ text: "Comment restored", label: "Delete again" });

    act(() => lastAction().onClick());
    expect(strip).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(setStatus).toHaveBeenLastCalledWith("deleted"));
  });

  it("sends Undo only after the status call before it has finished", async () => {
    const calls: string[] = [];
    let release: () => void = () => undefined;
    const setStatus = vi.fn((status: string) => {
      calls.push(status);
      return calls.length === 1
        ? new Promise<void>((r) => {
            release = r;
          })
        : Promise.resolve();
    });
    runThreadAction({ kind: "delete", strip: vi.fn(), restore: vi.fn(), setStatus, t });
    act(() => lastAction().onClick()); // Undo while "deleted" is still in flight
    await Promise.resolve();
    expect(calls).toEqual(["deleted"]);
    release();
    await waitFor(() => expect(calls).toEqual(["deleted", "open"]));
  });

  it("does not undo twice", () => {
    const restore = vi.fn();
    runThreadAction({ kind: "delete", strip: vi.fn(), restore, setStatus: vi.fn().mockResolvedValue(undefined), t });
    const { onClick } = lastAction();
    act(() => onClick());
    act(() => onClick());
    expect(restore).toHaveBeenCalledTimes(1);
  });
});
