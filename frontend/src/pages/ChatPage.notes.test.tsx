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
  NoteEditor: ({ noteId }: { noteId: number }) => (
    <div data-testid="note-editor">
      note {noteId}
      <div className="bn-block-outer" data-id={`b${noteId}`} />
    </div>
  ),
}));

import { ChatPage } from "./ChatPage";

const PAPER_TITLE = "Toward a Predictive eXtended Reality Teleoperation System";
const CHAT_TITLE = "Where does the XR latency drop come from?";

function note(id: number, kind: string, target: string, title: string) {
  return {
    note: { id, target_kind: kind, target_id: target, title, revision: 1, updated_at: "2026-11-16T00:00:00Z" },
    blocks: [],
    shared_by: kind === "paper" ? 2 : 1,
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
beforeAll(async () => {
  server.listen({ onUnhandledRequest: "bypass" });
  // The Notes pane is a lazy chunk: load it once here, so the first test
  // does not race its cold import (seconds under load) against findBy.
  await import("@/components/notes/NotesPane");
});
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
  useCanvasStore.setState({ open: false, width: 600, activePaperId: null, readerView: null, readingSection: null });
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 42);
  useChatStore.setState((s) => ({
    sessions: s.sessions.map((x) => (x.id === sid ? { ...x, title: CHAT_TITLE } : x)),
  }));
});

const notesButton = () => screen.getByRole("button", { name: "Notes" });
const notesPane = () => screen.queryByRole("region", { name: "Notes" });

describe("ChatPage Notes pane", () => {
  it("puts the notes toolbar under the tabs, sized by the pane (a container query)", async () => {
    render(<ChatPage />);
    await userEvent.click(notesButton());
    const pane = await screen.findByRole("region", { name: "Notes" });
    expect(pane).toHaveClass("@container");
    // The toolbar is in the lazy NotesPane: it mounts after the region.
    const bar = await within(pane).findByRole("toolbar", { name: "Note tools" });
    await screen.findByText("note 12");
    expect(within(bar).getByRole("button", { name: "Focus" })).toBeEnabled();
    // A chat's note has no paper to follow.
    expect(within(bar).queryByRole("button", { name: /Follow/ })).toBeNull();
    await userEvent.click(within(bar).getByRole("button", { name: "More" }));
    // The chat's note is linked to this chat only.
    expect(await screen.findByRole("menu")).toHaveTextContent("Shared by 1 chat · revision 1");
  });

  it("Follow works while the reader shows the paper as a PDF (the PDF reports its section)", async () => {
    useCanvasStore.setState({ open: true, width: 600, activePaperId: 7, readerView: { paperId: 7, kind: "pdf" } });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    const bar = await screen.findByRole("toolbar", { name: "Note tools" });
    await screen.findByText("note 11");
    const follow = within(bar).getByRole("button", { name: /^Following/ });
    expect(follow).not.toHaveAttribute("aria-disabled");
    expect(follow).toHaveAttribute("aria-pressed", "true");
  });

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
    // The hidden tab's pane is out of reach, slid aside toward its tab.
    const notesPanel = (await screen.findByTestId("note-editor")).closest("[inert]");
    expect(notesPanel).not.toBeNull();
    expect(notesPanel).toHaveClass("translate-x-3", "opacity-0");
    // Tailwind v4 translate-x-* sets `translate`: the transition must cover it.
    expect(notesPanel?.className).toMatch(/transition-\[opacity,translate\]/);
    // Tabs name the panes they control.
    const readerPanel = screen.getByRole("tabpanel", { name: "Reader" });
    expect(reader).toHaveAttribute("aria-controls", readerPanel.id);
    expect(readerPanel).toContainElement(screen.getByTestId("reader"));
    expect(notes).toHaveAttribute("aria-controls", notesPanel?.id);
    expect(notesPanel).toHaveAttribute("role", "tabpanel");
    expect(notesPanel).toHaveAttribute("aria-labelledby", notes.id);
    // Arrow keys move to the next / previous tab.
    reader.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(notes).toHaveAttribute("aria-selected", "true");
    expect(notes).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(reader).toHaveAttribute("aria-selected", "true");
    expect(reader).toHaveFocus();
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
    expect((await screen.findByRole("region", { name: "Notes" })).parentElement).toHaveStyle({ width: "420px" });
    expect(screen.getByTestId("reader").closest("[inert]")).toBeNull();
    expect((await screen.findByTestId("note-editor")).closest("[inert]")).toBeNull();
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

  it("the divider leaves the reader its 320 px beside the 6 px divider", async () => {
    useCanvasStore.setState({ open: true, width: 900, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    const divider = screen.getByRole("separator", { name: "Resize Reader and Notes" });
    fireEvent.pointerDown(divider, { clientX: 500 });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 0 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
    expect(useNotesPaneStore.getState().notesWidth).toBe(900 - 320 - 6);
  });

  it("narrowing a side-by-side panel to tabs shows the Reader being dragged", async () => {
    useCanvasStore.setState({ open: true, width: 800, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    expect(useNotesPaneStore.getState().activeTab).toBe("notes");
    const edge = screen.getByRole("separator", { name: "Resize reference panel" });
    fireEvent.pointerDown(edge, { clientX: 300 });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 400 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
    expect(useCanvasStore.getState().width).toBe(700);
    const tabs = screen.getByRole("tablist", { name: "Right panel" });
    expect(within(tabs).getByRole("tab", { name: "Reader" })).toHaveAttribute("aria-selected", "true");
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

  it("Memory fades out over the reader instead of vanishing", async () => {
    render(<ChatPage />);
    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(screen.getByTestId("memory-overlay")).toHaveAttribute("data-state", "open");
    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    // Still there for its exit, fading out and out of reach ...
    const overlay = screen.getByTestId("memory-overlay");
    expect(overlay).toHaveAttribute("data-state", "closed");
    expect(overlay).toHaveAttribute("inert");
    // ... then gone.
    await waitFor(() => expect(screen.queryByTestId("memory-overlay")).toBeNull());
  });

  it("the side-by-side divider fades out with Notes instead of vanishing first", async () => {
    useCanvasStore.setState({ open: true, width: 900, activePaperId: 7 });
    render(<ChatPage />);
    await userEvent.click(notesButton());
    const divider = screen.getByRole("separator", { name: "Resize Reader and Notes" });
    expect(divider).toHaveAttribute("data-state", "open");
    act(() => useNotesPaneStore.getState().close());
    expect(divider).toBeInTheDocument();
    expect(divider).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(divider).not.toBeInTheDocument());
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
    const tabs = within(await screen.findByRole("region", { name: "Notes" })).getByRole("tablist", { name: "Notes" });
    const paperTab = await within(tabs).findByRole("tab", { name: PAPER_TITLE });
    expect(paperTab).toHaveAttribute("title", `Notes on: ${PAPER_TITLE}`);
    const chatTab = within(tabs).getByRole("tab", { name: "This chat" });
    expect(chatTab).toHaveAttribute("title", `Notes for this chat only: ${CHAT_TITLE}`);
    const notePanel = within(notesPane() as HTMLElement).getByRole("tabpanel");
    expect(paperTab).toHaveAttribute("aria-controls", notePanel.id);
    expect(chatTab).toHaveAttribute("aria-controls", notePanel.id);
    expect(notePanel).toHaveAttribute("aria-labelledby", paperTab.id);
    paperTab.focus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(chatTab).toHaveAttribute("aria-selected", "true");
    expect(chatTab).toHaveFocus();
    expect(notePanel).toHaveAttribute("aria-labelledby", chatTab.id);
    await userEvent.keyboard("{ArrowLeft}");
    expect(paperTab).toHaveAttribute("aria-selected", "true");
    await userEvent.click(chatTab);
    expect(chatTab).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("note 12")).toBeInTheDocument();
  });

  it("Open in notes shows the note's tab and brings the changed block into view", async () => {
    useCanvasStore.setState({ open: true, width: 600, activePaperId: 7 });
    const scrolled: Element[] = [];
    // jsdom has no scrollIntoView: give it one to watch.
    if (!("scrollIntoView" in Element.prototype)) Object.assign(Element.prototype, { scrollIntoView: () => undefined });
    const spy = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(function (this: Element) {
      scrolled.push(this);
    });
    try {
      render(<ChatPage />);
      // A paper is in the reader, so the pane would open on the paper's note.
      act(() => useNotesPaneStore.getState().openAt({ kind: "session", id: 42, noteId: 12, blockId: "b12" }));
      expect(useNotesPaneStore.getState()).toMatchObject({ open: true, activeTab: "notes" });
      expect(await screen.findByText("note 12")).toBeInTheDocument();
      await waitFor(() => expect(useNotesPaneStore.getState().request).toBeNull());
      const block = document.querySelector('.bn-block-outer[data-id="b12"]');
      expect(scrolled).toContain(block);
      expect(block).toHaveAttribute("data-flash");

      act(() => useNotesPaneStore.getState().openAt({ kind: "paper", id: 7, noteId: 11, blockId: "b11" }));
      expect(await screen.findByText("note 11")).toBeInTheDocument();
      await waitFor(() => expect(useNotesPaneStore.getState().request).toBeNull());
      expect(scrolled).toContain(document.querySelector('.bn-block-outer[data-id="b11"]'));
    } finally {
      spy.mockRestore();
    }
  });

  it("drops a request for another chat's note: the pane stays on what it shows", async () => {
    useCanvasStore.setState({ open: true, width: 600, activePaperId: 7 });
    render(<ChatPage />);
    act(() => useNotesPaneStore.getState().openAt({ kind: "session", id: 999, noteId: 50, blockId: "b50" }));
    await waitFor(() => expect(useNotesPaneStore.getState().request).toBeNull());
    expect(await screen.findByText("note 11")).toBeInTheDocument();
  });

  it("drops an Open in notes request that never shows its note, and an idea's at once", async () => {
    useCanvasStore.setState({ open: true, width: 600, activePaperId: 7 });
    render(<ChatPage />);
    // The pane shows note 11 for paper 7; a request for note 99 never lands.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      act(() => useNotesPaneStore.getState().openAt({ kind: "paper", id: 7, noteId: 99, blockId: "b99" }));
      expect(await screen.findByText("note 11")).toBeInTheDocument();
      expect(useNotesPaneStore.getState().request).not.toBeNull();
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(useNotesPaneStore.getState().request).toBeNull();
    } finally {
      vi.useRealTimers();
    }
    act(() => useNotesPaneStore.getState().openAt({ kind: "idea", id: 3, noteId: 40, blockId: "b1" }));
    await waitFor(() => expect(useNotesPaneStore.getState().request).toBeNull());
    expect(useNotesPaneStore.getState()).toMatchObject({ open: true, activeTab: "notes" });
  });
});
