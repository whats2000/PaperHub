/** Which note the pane opens on when the reader has not shown a paper:
 * the chat's last-viewed paper, else its first paper, else the hint. */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { NotesChangeCard } from "@/components/chat/NotesChangeCard";
import { API_BASE_URL } from "@/lib/api";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { ReferenceItem } from "@/types/domain";

vi.mock("@/components/notes/NoteEditor", () => ({
  NoteEditor: ({ noteId }: { noteId: number }) => (
    <div data-testid="note-editor" contentEditable suppressContentEditableWarning>
      note {noteId}
    </div>
  ),
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

import { NotesPane } from "./NotesPane";

const HINT = "Open a paper or start a chat to take notes.";
const TITLES: Record<number, string> = { 7: "RoboTTT", 8: "Gated Adapters", 9: "World Models" };

const ref = (id: number): ReferenceItem => ({
  papers_id: id + 100,
  paper_content_id: id,
  enabled: true,
  added_at: "2026-11-20T00:00:00Z",
  arxiv_id: null,
  title: TITLES[id] ?? `Paper ${id}`,
  year: 2026,
  kind: "arxiv",
  is_private: false,
});

/** Paper N's saved note is note 100+N; the chat's note is empty. */
let posts: string[] = [];
/** The chat's papers the server lists after an Add. */
let papersAfterAdd: ReferenceItem[] = [];
const server = setupServer(
  http.get(`${API_BASE_URL}/notes/target`, ({ request }) => {
    const url = new URL(request.url);
    const id = Number(url.searchParams.get("id"));
    if (url.searchParams.get("kind") === "paper") {
      return HttpResponse.json({
        note: { id: 100 + id, target_kind: "paper", target_id: String(id), title: TITLES[id], revision: 1, updated_at: "2026-11-20T00:00:00Z" },
        blocks: [],
        shared_by: 0,
      });
    }
    return HttpResponse.json({ note: null, blocks: [], shared_by: 0, default_title: "Chat" });
  }),
  http.get(`${API_BASE_URL}/papers`, () => HttpResponse.json(papersAfterAdd)),
  http.post("*", ({ request }) => {
    posts.push(new URL(request.url).pathname);
    return HttpResponse.json({});
  }),
  http.get("*", () => HttpResponse.json([])),
);
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterAll(() => server.close());
afterEach(() => server.resetHandlers());

let chatA = 0;
beforeEach(() => {
  posts = [];
  localStorage.clear();
  useChatStore.getState().reset();
  useNotesStore.setState({ notes: {}, byTarget: {} });
  useNotesPaneStore.setState({ open: true, activeTab: "notes", request: null, lastPaperByChat: {} });
  useCanvasStore.setState({
    open: false,
    activePaperId: null,
    readerView: null,
    readingSection: null,
    outsideByChat: {},
    closedTab: null,
    paperTitles: {},
    requestedPaperId: null,
  });
  chatA = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(chatA, 42);
});

/** The selected paper tab. */
const paperTab = () => screen.queryByRole("tab", { name: /RoboTTT|Gated Adapters|World Models/, selected: true });

describe("NotesPane: the paper it opens on", () => {
  it("opens the chat's first paper's note when the reader has shown none", async () => {
    useChatStore.getState().setReferences(42, [ref(8), ref(7)]);
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 108")).toBeInTheDocument();
    expect(paperTab()).toHaveAccessibleName("Gated Adapters");
    expect(paperTab()).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText(HINT)).toBeNull();
    // The reader is not forced open.
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("opens the chat's last-viewed paper's note over its first paper", async () => {
    useChatStore.getState().setReferences(42, [ref(8), ref(7)]);
    useCanvasStore.setState({ activePaperId: 7 });
    const { unmount } = render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    unmount();
    // Another chat, then back: the reader shows nothing now.
    useCanvasStore.setState({ activePaperId: null });
    act(() => {
      const other = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(other, 43);
    });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText(HINT)).toBeInTheDocument();
    act(() => {
      useChatStore.getState().selectSession(chatA);
    });
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    expect(paperTab()).toHaveAccessibleName("RoboTTT");
  });

  it("shows the hint only with no paper in the chat and an empty chat note", async () => {
    useChatStore.getState().setReferences(42, []);
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText(HINT)).toBeInTheDocument();
    expect(paperTab()).toBeNull();
  });

  it("creates nothing by looking", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () =>
        HttpResponse.json({ note: null, blocks: [], shared_by: 0, default_title: "Gated Adapters" }),
      ),
    );
    useChatStore.getState().setReferences(42, [ref(8)]);
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByTestId("note-editor")).toBeInTheDocument();
    expect(paperTab()).toHaveAccessibleName("Gated Adapters");
    expect(posts).toEqual([]);
  });

  it("switching chats never carries the other chat's reader paper over", async () => {
    // Chat A: the reader shows paper 7, so the pane shows its note.
    useChatStore.getState().setReferences(42, [ref(7), ref(8)]);
    useCanvasStore.setState({ open: true, activePaperId: 7 });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    // Chat B has paper 9. The reader still names A's paper for a moment
    // (it closes after the switch): B's own paper note shows.
    let chatB = 0;
    act(() => {
      chatB = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(chatB, 43);
      useChatStore.getState().setReferences(43, [ref(9)]);
    });
    expect(await screen.findByText("note 109")).toBeInTheDocument();
    expect(paperTab()).toHaveAccessibleName("World Models");
    expect(screen.queryByText("note 107")).toBeNull();
    // A brand-new chat with no papers: its own empty state, no paper tab.
    act(() => {
      useChatStore.getState().newSession();
    });
    expect(await screen.findByText(HINT)).toBeInTheDocument();
    expect(paperTab()).toBeNull();
    // Back to A (the reader is closed by now): A's last-viewed paper.
    act(() => {
      useCanvasStore.setState({ open: false, activePaperId: null });
      useChatStore.getState().selectSession(chatA);
    });
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    expect(paperTab()).toHaveAccessibleName("RoboTTT");
  });

  it("a chat whose papers are not loaded yet does not take the reader's paper", async () => {
    useChatStore.getState().setReferences(42, [ref(7)]);
    useCanvasStore.setState({ open: true, activePaperId: 7 });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    act(() => {
      const other = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(other, 44);
    });
    expect(await screen.findByText(HINT)).toBeInTheDocument();
    expect(paperTab()).toBeNull();
    // Its papers arrive: its own first paper, not the reader's.
    act(() => {
      useChatStore.getState().setReferences(44, [ref(8)]);
    });
    expect(await screen.findByText("note 108")).toBeInTheDocument();
  });

  it("the paper tab stays after the reader closes within the chat", async () => {
    useChatStore.getState().setReferences(42, [ref(8), ref(7)]);
    useCanvasStore.setState({ open: true, activePaperId: 7 });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    act(() => {
      useCanvasStore.getState().closeCanvas();
      useCanvasStore.setState({ activePaperId: null });
    });
    expect(screen.getByText("note 107")).toBeInTheDocument();
    expect(paperTab()).toHaveAccessibleName("RoboTTT");
  });

  it("the reader's paper still wins once it shows one", async () => {
    useChatStore.getState().setReferences(42, [ref(8), ref(7)]);
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 108")).toBeInTheDocument();
    act(() => {
      useCanvasStore.setState({ activePaperId: 7 });
    });
    expect(await screen.findByText("note 107")).toBeInTheDocument();
  });

  it("Open in notes on an agent's change card lands on the changed paper", async () => {
    useChatStore.getState().setReferences(42, [ref(8), ref(7), ref(9)]);
    useCanvasStore.setState({ activePaperId: 7 });
    server.use(
      http.get(`${API_BASE_URL}/notes/change-sets/cs_1`, () =>
        HttpResponse.json({
          id: "cs_1",
          note_id: 109,
          status: "applied",
          reapplied_by: null,
          head: { id: "cs_1", status: "applied" },
          note: { target_kind: "paper", target_id: "9", title: "World Models" },
          changes: [{ kind: "added", block_id: "b1", text: "x" }],
        }),
      ),
    );
    render(
      <>
        <NotesChangeCard
          data={{
            change_set_id: "cs_1",
            note_id: 109,
            note_title: "World Models",
            summary: { added: 1, updated: 0, deleted: 0, suggested: 0, rejected: 0, section: null },
            revision: 2,
          }}
        />
        <NotesPane onClose={() => undefined} />
      </>,
    );
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    const open = await screen.findByRole("button", { name: /Open in notes/ });
    await vi.waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    // The reader has not caught up yet (it is mocked away): the pane still
    // lands on the changed paper's note.
    expect(await screen.findByText("note 109")).toBeInTheDocument();
    const pane = screen.getByRole("region", { name: "Notes" });
    expect(within(pane).getByRole("tab", { name: "World Models" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("NotesPane: paper tabs shared with the reader", () => {
  const strip = () => within(screen.getByRole("region", { name: "Notes" })).getByRole("tablist", { name: "Notes" });
  const shellOf = (name: string) => within(strip()).getByRole("tab", { name }).closest("[data-paper-tab]") as HTMLElement;

  it("draws every paper of the chat in the reader's order; picking one moves the reader", async () => {
    useChatStore.getState().setReferences(42, [ref(8), ref(7)]);
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 108")).toBeInTheDocument();
    expect(within(strip()).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Gated Adapters", "RoboTTT"]);
    await userEvent.click(within(strip()).getByRole("tab", { name: "RoboTTT" }));
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    expect(paperTab()).toHaveAccessibleName("RoboTTT");
    // The reader follows without being forced open.
    expect(useCanvasStore.getState().requestedPaperId).toBe(7);
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("a paper opened by a link is a dashed, temporary tab here too; Add makes it solid", async () => {
    useChatStore.getState().setReferences(42, [ref(7)]);
    papersAfterAdd = [ref(7), ref(9)];
    useCanvasStore.setState({
      open: true,
      activePaperId: 9,
      outsideByChat: { [chatA]: [{ paperId: 9, preview: true }] },
      paperTitles: { 9: "World Models" },
    });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 109")).toBeInTheDocument();
    expect(shellOf("World Models")).toHaveAttribute("data-dashed", "true");
    expect(shellOf("World Models")).toHaveAttribute("data-preview", "true");
    await userEvent.click(within(shellOf("World Models")).getByRole("button", { name: /Add/ }));
    await vi.waitFor(() => expect(shellOf("World Models")).toHaveAttribute("data-dashed", "false"));
    expect(posts).toContain("/papers/from-library");
    expect(useCanvasStore.getState().outsideByChat[chatA]).toEqual([]);
  });

  it("× closes the temporary tab in both panes, landing on the neighbouring paper", async () => {
    useChatStore.getState().setReferences(42, [ref(7)]);
    useCanvasStore.setState({
      activePaperId: 9,
      outsideByChat: { [chatA]: [{ paperId: 9, preview: true }] },
      paperTitles: { 9: "World Models" },
    });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 109")).toBeInTheDocument();
    await userEvent.click(within(strip()).getByRole("button", { name: "Close World Models" }));
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    expect(useCanvasStore.getState().closedTab).toMatchObject({ chat: chatA, paperId: 9, neighbour: 7 });
    expect(useCanvasStore.getState().outsideByChat[chatA]).toEqual([]);
  });

  it("a tab closed in the reader closes here too", async () => {
    useChatStore.getState().setReferences(42, [ref(7)]);
    useCanvasStore.setState({
      activePaperId: 9,
      outsideByChat: { [chatA]: [{ paperId: 9, preview: true }] },
      paperTitles: { 9: "World Models" },
    });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 109")).toBeInTheDocument();
    act(() => useCanvasStore.getState().closeOutside(chatA, 9, 7));
    expect(await screen.findByText("note 107")).toBeInTheDocument();
  });

  it("editing the temporary note keeps its tab; so does a double click", async () => {
    useChatStore.getState().setReferences(42, [ref(7)]);
    useCanvasStore.setState({
      activePaperId: 9,
      outsideByChat: { [chatA]: [{ paperId: 9, preview: true }, { paperId: 8, preview: false }] },
      paperTitles: { 9: "World Models", 8: "Gated Adapters" },
    });
    render(<NotesPane onClose={() => undefined} />);
    const editor = await screen.findByText("note 109");
    fireEvent(editor, new InputEvent("beforeinput", { bubbles: true, inputType: "insertText", data: "x" }));
    const nine = () => useCanvasStore.getState().outsideByChat[chatA]?.find((t) => t.paperId === 9);
    expect(nine()).toEqual({ paperId: 9, preview: false });
    // A kept tab is not italic.
    expect(shellOf("World Models")).toHaveAttribute("data-preview", "false");
    // The next link opens a temporary tab beside it; a double click keeps that one.
    act(() => {
      useCanvasStore.setState({ paperTitles: { 9: "World Models", 8: "Gated Adapters", 10: "Paper 10" } });
    });
    act(() => useCanvasStore.getState().showOutside(chatA, 10));
    await userEvent.dblClick(within(strip()).getByRole("tab", { name: "Paper 10" }));
    expect(useCanvasStore.getState().outsideByChat[chatA]?.every((t) => !t.preview)).toBe(true);
  });

  it("another chat draws its own papers, never this chat's dashed tabs", async () => {
    useChatStore.getState().setReferences(42, [ref(7)]);
    useCanvasStore.setState({
      outsideByChat: { [chatA]: [{ paperId: 9, preview: false }] },
      paperTitles: { 9: "World Models" },
    });
    render(<NotesPane onClose={() => undefined} />);
    expect(await screen.findByText("note 107")).toBeInTheDocument();
    expect(within(strip()).getByRole("tab", { name: "World Models" })).toBeInTheDocument();
    act(() => {
      const other = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(other, 43);
      useChatStore.getState().setReferences(43, [ref(8)]);
    });
    expect(await screen.findByText("note 108")).toBeInTheDocument();
    expect(within(strip()).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Gated Adapters"]);
  });
});
