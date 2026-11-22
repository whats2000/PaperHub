/** Which note the pane opens on when the reader has not shown a paper:
 * the chat's last-viewed paper, else its first paper, else the hint. */
import { act, render, screen, within } from "@testing-library/react";
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
  NoteEditor: ({ noteId }: { noteId: number }) => <div data-testid="note-editor">note {noteId}</div>,
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
  useCanvasStore.setState({ open: false, activePaperId: null, readerView: null, readingSection: null });
  chatA = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(chatA, 42);
});

const paperTab = () => screen.queryByRole("tab", { name: /RoboTTT|Gated Adapters|World Models/ });

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
