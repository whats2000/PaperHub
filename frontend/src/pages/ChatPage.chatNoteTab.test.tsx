import { act, render, screen, waitFor, within } from "@testing-library/react";
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

const CHAT_TITLE = "Where does the XR latency drop come from?";
const PAPER_TITLE = "Toward a Predictive eXtended Reality Teleoperation System";

const textBlock = (id: string, text: string, note: number) => ({
  id,
  note_id: note,
  parent_id: null,
  type: "paragraph",
  props: {},
  content: [{ type: "text", text, styles: {} }],
  text,
  author: "user",
  section_key: null,
  changed_rev: 1,
});

/** The chat note the fake server holds: `null` = no note yet. */
let chatNote: { title: string; blocks: ReturnType<typeof textBlock>[] } | null = null;

function noteBody(id: number, kind: string, target: string, title: string, blocks: unknown[]) {
  return {
    note: { id, target_kind: kind, target_id: target, title, revision: 1, updated_at: "2026-11-16T00:00:00Z" },
    blocks,
    shared_by: 1,
  };
}

const paperNote = () => noteBody(11, "paper", "7", PAPER_TITLE, [textBlock("p1", "paper note", 11)]);

const server = setupServer(
  http.get(`${API_BASE_URL}/notes/target`, ({ request }) => {
    const url = new URL(request.url);
    if (url.searchParams.get("kind") === "paper") return HttpResponse.json(paperNote());
    return chatNote === null
      ? HttpResponse.json({ note: null, blocks: [], shared_by: 0, default_title: CHAT_TITLE })
      : HttpResponse.json(noteBody(12, "session", "42", chatNote.title, chatNote.blocks));
  }),
  http.get(`${API_BASE_URL}/notes/12`, () =>
    HttpResponse.json(noteBody(12, "session", "42", chatNote?.title ?? "", chatNote?.blocks ?? [])),
  ),
  http.get(`${API_BASE_URL}/sessions`, () =>
    HttpResponse.json([
      { id: 42, title: CHAT_TITLE, created_at: "2026-11-16T00:00:00Z", updated_at: "2026-11-16T00:00:00Z", message_count: 0 },
    ]),
  ),
  http.get("*", () => HttpResponse.json([])),
);
beforeAll(async () => {
  server.listen({ onUnhandledRequest: "bypass" });
  await import("@/components/notes/NotesPane");
});
afterAll(() => server.close());
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

beforeEach(() => {
  chatNote = null;
  localStorage.clear();
  useChatStore.getState().reset();
  useNotesStore.setState({ notes: {}, byTarget: {} });
  useNotesPaneStore.setState({ open: false, activeTab: "reader", notesWidth: NOTES_DEFAULT_WIDTH });
  useCanvasStore.setState({ open: false, width: 900, activePaperId: 7, readerView: null, readingSection: null });
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 42);
  useChatStore.setState((s) => ({
    sessions: s.sessions.map((x) => (x.id === sid ? { ...x, title: CHAT_TITLE } : x)),
  }));
});

const openNotes = async () => {
  await userEvent.click(screen.getByRole("button", { name: "Notes" }));
  return screen.findByRole("region", { name: "Notes" });
};
const chatTab = (pane: HTMLElement) => within(pane).queryByRole("tab", { name: "This chat" });

describe("the This chat tab", () => {
  it("is absent while the chat has no note; the paper's note shows alone", async () => {
    render(<ChatPage />);
    const pane = await openNotes();
    expect(await screen.findByText("note 11")).toBeInTheDocument();
    expect(chatTab(pane)).toBeNull();
  });

  it("is absent for an empty note (auto title only), shown once the user names it", async () => {
    chatNote = { title: "", blocks: [] };
    render(<ChatPage />);
    const pane = await openNotes();
    await screen.findByText("note 11");
    await waitFor(() => expect(useNotesStore.getState().byTarget["session:42"]).toBe(12));
    expect(chatTab(pane)).toBeNull();
    chatNote = { title: "My XR notes", blocks: [] };
    await act(async () => {
      await useNotesStore.getState().loadTarget("session", 42);
    });
    expect(await within(pane).findByRole("tab", { name: "This chat" })).toBeInTheDocument();
  });

  it("is there when the note already has text", async () => {
    chatNote = { title: "", blocks: [textBlock("b1", "hello", 12)] };
    render(<ChatPage />);
    const pane = await openNotes();
    expect(await within(pane).findByRole("tab", { name: "This chat" })).toBeInTheDocument();
  });

  it("appears once the first block is written, with the tab motion", async () => {
    render(<ChatPage />);
    const pane = await openNotes();
    await screen.findByText("note 11");
    expect(chatTab(pane)).toBeNull();
    chatNote = { title: "", blocks: [textBlock("b1", "first", 12)] };
    await act(async () => {
      await useNotesStore.getState().loadTarget("session", 42);
    });
    const tab = await within(pane).findByRole("tab", { name: "This chat" });
    expect(tab).toHaveClass("animate-in", "motion-reduce:animate-none");
  });

  it("appears when the agent writes the chat's note (notes_changed)", async () => {
    render(<ChatPage />);
    const pane = await openNotes();
    await screen.findByText("note 11");
    await waitFor(() => expect(useNotesStore.getState().byTarget["session:42"]).toBeDefined());
    expect(chatTab(pane)).toBeNull();
    chatNote = { title: "", blocks: [textBlock("b1", "from the agent", 12)] };
    act(() => {
      useChatStore.getState().applyAgentEvent(useChatStore.getState().activeSessionId!, 7, "notes_changed", { note_id: 12 });
    });
    expect(await within(pane).findByRole("tab", { name: "This chat" })).toBeInTheDocument();
  });

  it("re-evaluates when the chat changes", async () => {
    chatNote = { title: "", blocks: [textBlock("b1", "hello", 12)] };
    render(<ChatPage />);
    const pane = await openNotes();
    await within(pane).findByRole("tab", { name: "This chat" });
    chatNote = null;
    act(() => {
      const other = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(other, 43);
    });
    await waitFor(() => expect(chatTab(pane)).toBeNull());
  });
});

describe("the This chat tab while it is selected", () => {
  it("stays when its text is deleted, and goes once the paper tab is chosen", async () => {
    chatNote = { title: "", blocks: [textBlock("b1", "hello", 12)] };
    render(<ChatPage />);
    const pane = await openNotes();
    await userEvent.click(await within(pane).findByRole("tab", { name: "This chat" }));
    expect(await screen.findByText("note 12")).toBeInTheDocument();
    chatNote = { title: "", blocks: [] };
    act(() => {
      useNotesStore.setState((s) => ({
        notes: { ...s.notes, 12: { ...s.notes[12]!, blocks: [] } },
      }));
    });
    // No jump: the tab and the editor stay while the user is in them.
    await new Promise((r) => setTimeout(r, 400));
    expect(within(pane).getByRole("tab", { name: "This chat" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("note 12")).toBeInTheDocument();
    // Choosing the paper tab re-evaluates: the empty chat tab leaves.
    await userEvent.click(within(pane).getByRole("tab", { name: PAPER_TITLE }));
    expect(await screen.findByText("note 11")).toBeInTheDocument();
    expect(chatTab(pane)).toHaveClass("animate-out", "motion-reduce:animate-none");
    await waitFor(() => expect(chatTab(pane)).toBeNull());
  });
});
