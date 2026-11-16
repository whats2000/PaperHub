import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { flushPendingThreadActions, runThreadAction } from "@/components/notes/commentActions";
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
afterEach(() => flushPendingThreadActions());

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
  onDismiss: () => void;
  onAutoClose: () => void;
}
const toasts = () => toastFn.mock.calls as [string, ToastOpts][];

/** The last toast shown: its text and its action button. */
function lastAction() {
  const [text, opts] = toasts().at(-1)!;
  return { text, ...opts.action };
}

describe("Resolve and Delete with Undo", () => {
  it("Resolve → Undo → Resolve again", () => {
    const restore = vi.fn();
    const strip = vi.fn(() => restore);
    const setStatus = vi.fn().mockResolvedValue(undefined);
    runThreadAction({ kind: "resolve", strip, setStatus, t });

    expect(strip).toHaveBeenCalledTimes(1);
    expect(setStatus).toHaveBeenLastCalledWith("resolved");
    expect(lastAction()).toMatchObject({ text: "Comment resolved", label: "Undo" });

    act(() => lastAction().onClick());
    expect(restore).toHaveBeenCalledTimes(1);
    expect(setStatus).toHaveBeenLastCalledWith("open");
    expect(lastAction()).toMatchObject({ text: "Comment restored", label: "Resolve again" });

    act(() => lastAction().onClick());
    expect(strip).toHaveBeenCalledTimes(2);
    expect(setStatus).toHaveBeenLastCalledWith("resolved");
    expect(lastAction()).toMatchObject({ text: "Comment resolved", label: "Undo" });
  });

  it("Delete waits for its toast: Undo cancels it, and the server hears nothing", () => {
    const restore = vi.fn();
    const setStatus = vi.fn().mockResolvedValue(undefined);
    runThreadAction({ kind: "delete", strip: () => restore, setStatus, t });
    expect(lastAction()).toMatchObject({ text: "Comment deleted", label: "Undo" });
    expect(setStatus).not.toHaveBeenCalled();

    act(() => lastAction().onClick());
    expect(restore).toHaveBeenCalled();
    expect(lastAction()).toMatchObject({ text: "Comment restored", label: "Delete again" });
    // The undone toast closing must not delete.
    toasts()[0]![1].onDismiss();
    expect(setStatus).not.toHaveBeenCalled();
  });

  it("Delete is sent once its toast runs out, and when the editor goes", () => {
    const setStatus = vi.fn().mockResolvedValue(undefined);
    runThreadAction({ kind: "delete", strip: () => vi.fn(), setStatus, t });
    toasts()[0]![1].onAutoClose();
    toasts()[0]![1].onDismiss();
    expect(setStatus).toHaveBeenCalledTimes(1);
    expect(setStatus).toHaveBeenCalledWith("deleted");

    const second = vi.fn().mockResolvedValue(undefined);
    runThreadAction({ kind: "delete", strip: () => vi.fn(), setStatus: second, t });
    flushPendingThreadActions();
    expect(second).toHaveBeenCalledWith("deleted");
  });
});
