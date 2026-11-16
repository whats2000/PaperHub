import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const { toast } = vi.hoisted(() => ({
  toast: Object.assign(vi.fn((..._args: unknown[]) => "toast-1"), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }),
}));
vi.mock("sonner", () => ({ toast }));

import { API_BASE_URL } from "@/lib/api";
import i18n from "@/lib/i18n";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useIdeationStore } from "@/store/ideation";
import { useNavStore } from "@/store/nav";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import type { NoteOpsBody } from "@/types/notes";
import { FULL_TEXT_SECTION, type GraphResponse, type Idea, type IdeaPatch } from "@/types/ideation";

import { IdeaDetail } from "./IdeaDetail";
import { IdeaKanban } from "./IdeaKanban";

const base = (over: Partial<Idea> = {}): Idea => ({
  id: 5,
  problem_id: null,
  source_doc: 1,
  source_section: "Region Proposal Networks",
  source_chunk_ids: [10, 11, 12],
  target_doc: 2,
  target_section: "Few-shot prompting",
  target_chunk_ids: [20],
  title: "Anchor boxes as prompts",
  rationale: "Both enumerate candidates.",
  status: "trying",
  prev_status: null,
  note: "",
  dismiss_reasons: [],
  session_ids: [],
  created_at: "2026-11-05T10:00:00Z",
  ...over,
});

const graph = (idea: Idea): GraphResponse => ({
  documents: [
    { id: 1, title: "Faster R-CNN", domains: [1], idea_count: 1, is_private: false },
    { id: 2, title: "GPT-3", domains: [2], idea_count: 1, is_private: false },
  ],
  domains: [
    { id: 1, name: "Vision", color_index: 0 },
    { id: 2, name: "Language", color_index: 1 },
  ],
  citations: [],
  library_count: 2,
  private_count: 0,
  ideas: [idea],
  problem: null,
});

// The server's copy of idea 5, mutated by the handlers like the real API.
let server_idea: Idea;
const patches: IdeaPatch[] = [];
const patchedIds: number[] = [];
// Optional delay for PATCH responses (to hold a save in flight).
let patchDelayMs = 0;
const undoCalls: number[] = [];
const chatCalls: number[] = [];
const noteTargets: string[] = [];
const postedOps: NoteOpsBody[] = [];
let noteFails = false;
let contentOnly = false;
const noteWords = "Try it on the eval set";

const server = setupServer(
  http.get(`${API_BASE_URL}/marks/colors`, () => HttpResponse.json([])),
  http.get(`${API_BASE_URL}/notes/target`, ({ request }) => {
    const u = new URL(request.url);
    const ideaId = Number(u.searchParams.get("id"));
    noteTargets.push(`${u.searchParams.get("kind")}:${ideaId}`);
    if (noteFails) return HttpResponse.json({ detail: "boom" }, { status: 500 });
    const noteId = 65 + ideaId; // idea 5 -> note 70, idea 6 -> note 71
    return HttpResponse.json({
      note: { id: noteId, target_kind: "idea", target_id: String(ideaId), title: "", revision: 2, updated_at: "2026-11-16T00:00:00Z" },
      blocks: [
        {
          id: "n1", note_id: noteId, parent_id: null, type: "paragraph", props: {},
          // `contentOnly`: the stored text column is empty, the words are only in the content.
          content: [{ type: "text", text: noteWords, styles: {} }],
          text: contentOnly ? "" : noteWords, author: "user", section_key: null, changed_rev: 1,
        },
      ],
      suggestions: [],
      comments: [],
      shared_by: 0,
    });
  }),
  http.post(`${API_BASE_URL}/notes/:noteId/ops`, async ({ request, params }) => {
    postedOps.push((await request.json()) as NoteOpsBody);
    return HttpResponse.json({
      note_id: Number(params.noteId), revision: 3, change_set_id: null, applied: [], suggested: [], rejected: [], reparented: [],
    });
  }),
  http.patch(`${API_BASE_URL}/ideation/ideas/:id`, async ({ request, params }) => {
    const body = (await request.json()) as IdeaPatch;
    patches.push(body);
    patchedIds.push(Number(params.id));
    if (patchDelayMs) await new Promise((r) => setTimeout(r, patchDelayMs));
    if (body.status === "dismissed") {
      server_idea = {
        ...server_idea,
        prev_status: server_idea.status,
        status: "dismissed",
        dismiss_reasons: body.dismiss_reasons ?? [],
      };
    } else {
      server_idea = { ...server_idea, ...(body.status ? { status: body.status } : {}), ...(body.note !== undefined ? { note: body.note } : {}) };
    }
    return HttpResponse.json(server_idea);
  }),
  http.post(`${API_BASE_URL}/ideation/ideas/:id/undo-dismiss`, ({ params }) => {
    undoCalls.push(Number(params.id));
    server_idea = { ...server_idea, status: server_idea.prev_status ?? "new", prev_status: null, dismiss_reasons: [] };
    return HttpResponse.json(server_idea);
  }),
  http.post(`${API_BASE_URL}/ideation/ideas/:id/chat`, ({ params }) => {
    chatCalls.push(Number(params.id));
    return HttpResponse.json({
      session_id: 900 + chatCalls.length,
      title: "Anchor boxes as prompts",
      prefill: "Let's dig into the idea “Anchor boxes as prompts”.",
      paper_content_ids: [1, 2],
    });
  }),
);

// jsdom has no layout: record the reveal scroll instead.
const scrollIntoView = vi.fn();
beforeAll(() => {
  Element.prototype.scrollIntoView = scrollIntoView;
  server.listen({ onUnhandledRequest: "error" });
});
afterAll(() => server.close());
afterEach(() => {
  clearPendingNoteOps();
  server.resetHandlers();
});

/** IdeaDetail fed from the store, the way the drawer renders it. */
function Harness({ onReadSection }: { onReadSection?: (docId: number, chunkIds: number[]) => void }) {
  const idea = useIdeationStore((s) => {
    const sel = s.selected;
    return sel?.kind === "idea" ? s.graph?.ideas.find((i) => i.id === sel.id) : undefined;
  });
  return idea ? <IdeaDetail key={idea.id} idea={idea} onReadSection={onReadSection} /> : null;
}

function setup(idea: Idea = base(), onReadSection?: (docId: number, chunkIds: number[]) => void) {
  server_idea = idea;
  useIdeationStore.setState({
    scope: "explore",
    filter: "active",
    graph: graph(idea),
    selected: { kind: "idea", id: idea.id },
    leavingIdeaIds: [],
  });
  return render(<Harness onReadSection={onReadSection} />);
}

beforeEach(() => {
  patches.length = 0;
  patchedIds.length = 0;
  patchDelayMs = 0;
  undoCalls.length = 0;
  chatCalls.length = 0;
  noteTargets.length = 0;
  postedOps.length = 0;
  noteFails = false;
  contentOnly = false;
  useNotesStore.setState({ notes: {}, byTarget: {}, reloads: {}, retrying: {}, drafts: {}, colorNames: {} });
  toast.mockClear();
  scrollIntoView.mockClear();
  toast.dismiss.mockClear();
  useNavStore.setState({ page: "ideas" });
  useChatStore.setState({ sessions: [], activeSessionId: null, composerDraft: "" });
});

describe("IdeaDetail", () => {
  it("opens the idea's note in the notes editor", async () => {
    setup();
    expect(await screen.findByTestId("note-editor")).toBeInTheDocument();
    expect(noteTargets).toEqual(["idea:5"]);
    expect(await screen.findByText("Try it on the eval set")).toBeInTheDocument();
    // The plain textarea and its own autosave are gone.
    expect(screen.queryByTestId("note-state")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Discussed with my advisor/)).not.toBeInTheDocument();
  });

  it("posts note ops, never an idea PATCH, when the note is edited", async () => {
    setup();
    await screen.findByText("Try it on the eval set");
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { commands: { insertContentAt: (pos: number, text: string) => boolean } };
    };
    act(() => {
      pm.editor.commands.insertContentAt(25, "!");
    });
    await act(async () => {
      await useNotesStore.getState().flush(70);
    });
    // Under load the editor may queue the edit after that flush: it then
    // goes out on the next save.
    await waitFor(() => expect(postedOps).toHaveLength(1), { timeout: 2500 });
    expect(postedOps[0]?.ops).toEqual([expect.objectContaining({ op: "update", block_id: "n1" })]);
    expect(patches).toEqual([]);
    // The kanban / drawer excerpt follows the edit.
    expect(useIdeationStore.getState().graph?.ideas[0]?.note).toBe("Try it on the eval set!");
  });

  it("posts a pending note edit once when the drawer switches to another idea", async () => {
    setup();
    useIdeationStore.setState((s) => ({
      graph: s.graph && { ...s.graph, ideas: [...s.graph.ideas, base({ id: 6, title: "Other idea" })] },
    }));
    await screen.findByText(noteWords);
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { commands: { insertContentAt: (pos: number, text: string) => boolean } };
    };
    act(() => {
      pm.editor.commands.insertContentAt(25, "!");
    });
    act(() => useIdeationStore.setState({ selected: { kind: "idea", id: 6 } }));
    await waitFor(() => expect(postedOps).toHaveLength(1), { timeout: 2500 });
    expect(postedOps[0]?.ops).toEqual([expect.objectContaining({ op: "update", block_id: "n1" })]);
    await new Promise((r) => setTimeout(r, 800));
    expect(postedOps).toHaveLength(1);
    expect(patches).toEqual([]);
  });

  it("keeps the list's note when a block's words are only in its content", async () => {
    contentOnly = true;
    // The list endpoint already joined `text or block_text(...)`: the same string.
    setup(base({ note: noteWords }));
    await screen.findByText(noteWords);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(useIdeationStore.getState().graph?.ideas[0]?.note).toBe(noteWords);
  });

  it("puts the note's text back when a graph refetch brings a stale one", async () => {
    setup(base({ note: noteWords }));
    await screen.findByText(noteWords);
    act(() =>
      useIdeationStore.setState((s) => ({
        graph: s.graph && { ...s.graph, ideas: s.graph.ideas.map((i) => ({ ...i, note: "stale server text" })) },
      })),
    );
    await waitFor(() => expect(useIdeationStore.getState().graph?.ideas[0]?.note).toBe(noteWords));
  });

  it("shows the edited note on the kanban card", async () => {
    server_idea = base();
    useIdeationStore.setState({
      scope: "explore", filter: "active", graph: graph(server_idea), selected: { kind: "idea", id: 5 }, leavingIdeaIds: [],
    });
    function Both() {
      const g = useIdeationStore((s) => s.graph)!;
      return (
        <>
          <IdeaKanban graph={g} ideas={g.ideas} filter="active" selected={null} onSelect={() => {}} />
          <IdeaDetail key={5} idea={g.ideas[0]!} />
        </>
      );
    }
    render(<Both />);
    await screen.findByTestId("idea-note").then(() => screen.findAllByText(noteWords));
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { commands: { insertContentAt: (pos: number, text: string) => boolean } };
    };
    act(() => {
      pm.editor.commands.insertContentAt(25, "!");
    });
    await waitFor(() => expect(screen.getByTestId("idea-card-5")).toHaveTextContent(`${noteWords}!`));
  });

  it("fades the loading line out over the editor, and the editor in", async () => {
    setup();
    const status = await screen.findByTestId("idea-note-status");
    const editor = await screen.findByTestId("note-editor");
    expect(editor.className).toContain("fade-in");
    await waitFor(() => expect(status).toHaveAttribute("data-closed"));
    fireEvent.transitionEnd(status, { propertyName: "opacity" });
    expect(screen.queryByTestId("idea-note-status")).not.toBeInTheDocument();
  });

  it("says so, with a retry, when the note cannot be loaded", async () => {
    noteFails = true;
    setup();
    expect(await screen.findByText("Couldn't load the note.")).toBeInTheDocument();
    noteFails = false;
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Try it on the eval set")).toBeInTheDocument();
  });

  it("the toast Undo and the banner Undo undo once", async () => {
    const user = userEvent.setup();
    setup(base({ status: "saved" }));
    // Under the dismissed filter the drawer stays and shows the banner.
    useIdeationStore.setState({ filter: "dismissed" });
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await user.click(screen.getByRole("button", { name: "Not novel" }));
    await user.click(screen.getByRole("button", { name: "Dismiss idea" }));
    const banner = await screen.findByRole("button", { name: "Undo" });
    await user.click(banner);
    // The banner's Undo retires the toast; a late toast click is a no-op.
    expect(toast.dismiss).toHaveBeenCalledWith("toast-1");
    const [, opts] = toast.mock.calls[0] as [string, { action: { onClick: () => void } }];
    act(() => opts.action.onClick());
    await waitFor(() => expect(useIdeationStore.getState().graph?.ideas[0]?.status).toBe("saved"));
    expect(undoCalls).toEqual([5]);
  });

  it("needs a reason to dismiss, and Undo restores the exact previous status", async () => {
    const user = userEvent.setup();
    setup(base({ status: "trying" }));
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    const confirm = screen.getByRole("button", { name: "Dismiss idea" });
    expect(confirm).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Analogy doesn't hold" }));
    await user.type(screen.getByRole("textbox", { name: "Dismissal note" }), "Discrete by nature");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(patches).toEqual([
      { status: "dismissed", dismiss_reasons: ["analogy_fails"], dismiss_note: "Discrete by nature" },
    ]);
    const state = useIdeationStore.getState();
    expect(state.graph?.ideas[0]?.status).toBe("dismissed");
    // The idea fades out of the graph, and the drawer closes under the active filter.
    expect(state.leavingIdeaIds).toEqual([5]);
    expect(state.selected).toBeNull();

    const [, opts] = toast.mock.calls[0] as [string, { action: { label: string; onClick: () => void } }];
    expect(opts.action.label).toBe("Undo");
    act(() => {
      opts.action.onClick();
    });
    await waitFor(() => expect(useIdeationStore.getState().graph?.ideas[0]?.status).toBe("trying"));
    expect(undoCalls).toEqual([5]);
    // Undo goes through undo-dismiss, never a PATCH back to "new".
    expect(patches).toHaveLength(1);
    expect(useIdeationStore.getState().selected).toEqual({ kind: "idea", id: 5 });
  });

  it("collapses the reason form on cancel instead of removing it", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    const form = screen.getByTestId("dismiss-form");
    const expander = form.parentElement?.parentElement as HTMLElement;
    expect(expander).toHaveAttribute("data-state", "open");
    // The opened form is brought into view, bottom edge first.
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: "end" })));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    // Still mounted, now closing by height (grid rows 1fr -> 0fr).
    expect(screen.getByTestId("dismiss-form")).toBeInTheDocument();
    expect(expander).toHaveAttribute("data-state", "closed");
    expect(expander.className).toContain("grid-rows-[0fr]");
    expect(expander.className).toContain("transition-[grid-template-rows]");
    expect(patches).toEqual([]);
  });

  it("opens a chat: the backend session is added, the composer prefilled and the page switched", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Open a chat" }));
    await waitFor(() => expect(useNavStore.getState().page).toBe("chat"));
    const chat = useChatStore.getState();
    const session = chat.sessions.find((s) => s.backend_session_id === 901);
    expect(session).toMatchObject({ title: "Anchor boxes as prompts", idea_id: 5 });
    expect(chat.activeSessionId).toBe(session?.id);
    // Composed in the UI language from the idea, not the server's English prefill.
    expect(chat.composerDraft).toBe(
      "Let's dig into the idea “Anchor boxes as prompts”.\n" +
        "Mechanism source: “Faster R-CNN”, section Region Proposal Networks.\n" +
        "Applied to: “GPT-3”, section Few-shot prompting.\n" +
        "Why it might fit: Both enumerate candidates.\n" +
        "How would the mechanism carry over, and where could the analogy break?",
    );
    expect(useIdeationStore.getState().graph?.ideas[0]?.session_ids).toEqual([901]);
    expect(screen.getByRole("button", { name: "Continue chat" })).toBeInTheDocument();
  });

  it("prefills the opening message in the UI language (zh-TW)", async () => {
    const user = userEvent.setup();
    await i18n.changeLanguage("zh-TW");
    try {
      setup(base({ target_doc: null, target_section: null, target_chunk_ids: [] }));
      useIdeationStore.setState((s) => ({
        graph: s.graph && {
          ...s.graph,
          problem: { id: 7, text: "重新排序檢索到的圖片", tried: "", created_at: "", idea_count: 1, status: "ready" },
        },
      }));
      await user.click(screen.getByRole("button", { name: "開聊天深入" }));
      await waitFor(() => expect(useNavStore.getState().page).toBe("chat"));
      const draft = useChatStore.getState().composerDraft;
      expect(draft).toContain("我們來深入討論這個 idea：「Anchor boxes as prompts」");
      expect(draft).toContain("我的問題：重新排序檢索到的圖片");
      expect(draft).not.toContain("Let's dig");
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("falls back to the server's prefill when the graph lacks a document", async () => {
    const user = userEvent.setup();
    setup(base({ source_doc: 99 }));
    await user.click(screen.getByRole("button", { name: "Open a chat" }));
    await waitFor(() =>
      expect(useChatStore.getState().composerDraft).toBe("Let's dig into the idea “Anchor boxes as prompts”."),
    );
  });

  it("offers Open chat (not Continue) when the idea's chats aren't in the sidebar", async () => {
    const user = userEvent.setup();
    setup(base({ session_ids: [555] }));
    expect(screen.queryByRole("button", { name: "Continue chat" })).toBeNull();
    expect(screen.queryByRole("button", { name: "New chat" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Open a chat" }));
    await waitFor(() => expect(chatCalls).toEqual([5]));
  });

  it("New chat starts another linked chat even when one can be continued", async () => {
    const user = userEvent.setup();
    useChatStore.setState({
      sessions: [{ id: 3, title: "Old idea chat", messages: [], backend_session_id: 777, idea_id: 5 }],
      activeSessionId: null,
      composerDraft: "",
    });
    setup(base({ session_ids: [777] }));
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(chatCalls).toEqual([5]));
    expect(useChatStore.getState().sessions.some((s) => s.backend_session_id === 901)).toBe(true);
  });

  it("continues the idea's latest chat when it is in the sidebar", async () => {
    const user = userEvent.setup();
    useChatStore.setState({
      sessions: [{ id: 3, title: "Old idea chat", messages: [], backend_session_id: 777, idea_id: 5 }],
      activeSessionId: null,
      composerDraft: "",
    });
    setup(base({ session_ids: [777] }));
    await user.click(screen.getByRole("button", { name: "Continue chat" }));
    expect(useNavStore.getState().page).toBe("chat");
    expect(useChatStore.getState().activeSessionId).toBe(3);
    expect(chatCalls).toEqual([]);
  });

  it("opens a cited section in the Citation Canvas at its chunks", async () => {
    const user = userEvent.setup();
    useCanvasStore.setState({ open: false, requestedChunkId: null, requestedEndChunkId: null });
    setup();
    await user.click(screen.getByRole("button", { name: /Region Proposal Networks/ }));
    await waitFor(() => expect(useCanvasStore.getState().open).toBe(true));
    expect(useCanvasStore.getState()).toMatchObject({ requestedChunkId: 10, requestedEndChunkId: 12 });
    expect(useNavStore.getState().page).toBe("chat");
  });

  it("names a sectionless document's whole-text citation in the UI language and opens its body", async () => {
    const user = userEvent.setup();
    useCanvasStore.setState({ open: false, requestedChunkId: null, requestedEndChunkId: null });
    await i18n.changeLanguage("zh-TW");
    try {
      setup(base({ source_section: FULL_TEXT_SECTION, source_chunk_ids: [30, 31, 32] }));
      expect(screen.queryByText(FULL_TEXT_SECTION)).toBeNull();
      await user.click(screen.getByRole("button", { name: /全文/ }));
      await waitFor(() => expect(useCanvasStore.getState().open).toBe(true));
      expect(useCanvasStore.getState()).toMatchObject({ requestedChunkId: 30, requestedEndChunkId: 32 });
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("opens a cited section in the page's reader when it has one, not in a chat", async () => {
    const user = userEvent.setup();
    const onRead = vi.fn();
    useCanvasStore.setState({ open: false, requestedChunkId: null, requestedEndChunkId: null });
    const idea = base();
    setup(idea, onRead);
    await user.click(screen.getByRole("button", { name: /Region Proposal Networks/ }));
    expect(onRead).toHaveBeenCalledWith(idea.source_doc, idea.source_chunk_ids);
    expect(useCanvasStore.getState().open).toBe(false);
    expect(chatCalls).toEqual([]);
  });
});
