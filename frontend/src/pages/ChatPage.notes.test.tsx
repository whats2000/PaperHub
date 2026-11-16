import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { NOTES_DEFAULT_WIDTH, useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";

vi.mock("@/components/canvas/CitationCanvas", () => ({
  CitationCanvas: () => <div data-testid="reader">reader</div>,
}));
vi.mock("@/components/chat/MemoryManager", () => ({
  MemoryManager: () => <div data-testid="memory">memory</div>,
}));
vi.mock("@/components/slides/SlidesPanel", () => ({
  SlidesPanel: () => <div data-testid="slides">slides</div>,
}));
vi.mock("@/components/notes/NoteEditor", () => ({
  NoteEditor: ({ noteId }: { noteId: number }) => <div data-testid="note-editor">note {noteId}</div>,
}));

import { ChatPage } from "./ChatPage";

const PAPER_TITLE = "Toward a Predictive eXtended Reality Teleoperation System";
const CHAT_TITLE = "Where does the XR latency drop come from?";

function note(id: number, kind: string, target: string, title: string) {
  return {
    note: { id, target_kind: kind, target_id: target, title, revision: 1, updated_at: "2026-11-16T00:00:00Z" },
    blocks: [],
  };
}

const server = setupServer(
  http.get(`${API_BASE_URL}/notes/target`, ({ request }) => {
    const url = new URL(request.url);
    return url.searchParams.get("kind") === "paper"
      ? HttpResponse.json(note(11, "paper", "7", PAPER_TITLE))
      : HttpResponse.json(note(12, "session", "42", CHAT_TITLE));
  }),
  // The chat exists on the server too (the sidebar mirrors the server's list).
  http.get(`${API_BASE_URL}/sessions`, () =>
    HttpResponse.json([
      { id: 42, title: CHAT_TITLE, created_at: "2026-11-16T00:00:00Z", updated_at: "2026-11-16T00:00:00Z", message_count: 0 },
    ]),
  ),
  http.get("*", () => HttpResponse.json([])),
);
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterAll(() => server.close());
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

beforeEach(() => {
  localStorage.clear();
  useChatStore.getState().reset();
  useNotesStore.setState({ notes: {}, byTarget: {} });
  useNotesPaneStore.setState({ open: false, activeTab: "reader", notesWidth: NOTES_DEFAULT_WIDTH });
  useCanvasStore.setState({ open: false, width: 600, activePaperId: null });
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 42);
  useChatStore.setState((s) => ({
    sessions: s.sessions.map((x) => (x.id === sid ? { ...x, title: CHAT_TITLE } : x)),
  }));
});

const notesButton = () => screen.getByRole("button", { name: "Notes" });
const notesPane = () => screen.queryByRole("region", { name: "Notes" });

describe("ChatPage Notes pane", () => {
  it("the Notes button opens and closes the pane", async () => {
    render(<ChatPage />);
    expect(notesButton()).toHaveAttribute("aria-pressed", "false");
    expect(notesPane()).toBeNull();
    await userEvent.click(notesButton());
    expect(notesButton()).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("region", { name: "Notes" })).toBeInTheDocument();
    // Nothing else open: the chat's note.
    expect(await screen.findByText("note 12")).toBeInTheDocument();
    await userEvent.click(notesButton());
    expect(notesButton()).toHaveAttribute("aria-pressed", "false");
    // It slides out, out of reach, before it goes.
    const leaving = document.querySelector('section[aria-label="Notes"]')?.parentElement;
    expect(leaving).toHaveClass("animate-out", "slide-out-to-right-8", "motion-reduce:animate-none");
    expect(leaving).toHaveAttribute("inert");
    await waitFor(() => expect(document.querySelector('section[aria-label="Notes"]')).toBeNull());
  });

  it("shares a narrow panel with the reader as Reader | Notes tabs", async () => {
    useCanvasStore.setState({ open: true, width: 600, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    const tabs = screen.getByRole("tablist", { name: "Right panel" });
    const reader = within(tabs).getByRole("tab", { name: "Reader" });
    const notes = within(tabs).getByRole("tab", { name: "Notes" });
    expect(notes).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("separator", { name: "Resize Reader and Notes" })).toBeNull();
    await userEvent.click(reader);
    expect(reader).toHaveAttribute("aria-selected", "true");
    expect(useNotesPaneStore.getState().activeTab).toBe("reader");
    // The hidden tab's pane is out of reach.
    expect(screen.getByTestId("note-editor").closest("[inert]")).not.toBeNull();
    // The Notes button brings the hidden Notes tab forward instead of closing it.
    await userEvent.click(notesButton());
    expect(notes).toHaveAttribute("aria-selected", "true");
    expect(useNotesPaneStore.getState().open).toBe(true);
  });

  it("sits side by side with the reader from 760 px, Notes at its own width", async () => {
    useCanvasStore.setState({ open: true, width: 900, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    expect(screen.queryByRole("tablist", { name: "Right panel" })).toBeNull();
    expect(screen.getByRole("separator", { name: "Resize Reader and Notes" })).toBeInTheDocument();
    expect(notesPane()?.parentElement).toHaveStyle({ width: "420px" });
    expect(screen.getByTestId("reader").closest("[inert]")).toBeNull();
    expect(screen.getByTestId("note-editor").closest("[inert]")).toBeNull();
    // The paper's note while the reader shows a paper.
    expect(await screen.findByText("note 11")).toBeInTheDocument();
  });

  it("the middle divider resizes Notes; the left edge resizes only the reader", async () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(1400);
    useCanvasStore.setState({ open: true, width: 900, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    const divider = screen.getByRole("separator", { name: "Resize Reader and Notes" });
    fireEvent.pointerDown(divider, { clientX: 500 });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 440 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
    expect(useNotesPaneStore.getState().notesWidth).toBe(480);
    expect(useCanvasStore.getState().width).toBe(900);

    const edge = screen.getByRole("separator", { name: "Resize reference panel" });
    fireEvent.pointerDown(edge, { clientX: 300 });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 350 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
    expect(useCanvasStore.getState().width).toBe(850);
    expect(useNotesPaneStore.getState().notesWidth).toBe(480);

    // Closing and reopening keeps the dragged widths.
    await userEvent.click(notesButton());
    await userEvent.click(notesButton());
    expect(useNotesPaneStore.getState().notesWidth).toBe(480);
    expect(useCanvasStore.getState().width).toBe(850);
  });

  it("Memory and Notes close each other", async () => {
    render(<ChatPage />);
    await userEvent.click(notesButton());
    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(useNotesPaneStore.getState().open).toBe(false);
    expect(screen.getByRole("button", { name: "Memory" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(notesButton());
    expect(useNotesPaneStore.getState().open).toBe(true);
    expect(screen.getByRole("button", { name: "Memory" })).toHaveAttribute("aria-pressed", "false");
  });

  it("Slides and Notes share the panel", async () => {
    render(<ChatPage />);
    await userEvent.click(notesButton());
    await userEvent.click(screen.getByRole("button", { name: "Slides" }));
    expect(useNotesPaneStore.getState().open).toBe(true);
    const tabs = screen.getByRole("tablist", { name: "Right panel" });
    expect(within(tabs).getByRole("tab", { name: "Slides" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("slides")).toBeInTheDocument();
  });

  it("a citation brings the Reader tab forward", async () => {
    useCanvasStore.setState({ open: true, width: 600, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    expect(useNotesPaneStore.getState().activeTab).toBe("notes");
    act(() => useCanvasStore.getState().openCitation(5));
    expect(useNotesPaneStore.getState().activeTab).toBe("reader");
  });

  it("names the paper and the chat on the note tabs", async () => {
    useCanvasStore.setState({ open: true, width: 900, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    const tabs = within(notesPane() as HTMLElement).getByRole("tablist", { name: "Notes" });
    const paperTab = await within(tabs).findByRole("tab", { name: PAPER_TITLE });
    expect(paperTab).toHaveAttribute("title", `Notes on: ${PAPER_TITLE}`);
    const chatTab = within(tabs).getByRole("tab", { name: "This chat" });
    expect(chatTab).toHaveAttribute("title", `Notes for this chat only: ${CHAT_TITLE}`);
    await userEvent.click(chatTab);
    expect(chatTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("note 12")).toBeInTheDocument();
  });
});
