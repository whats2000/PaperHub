import { act, render, screen, waitFor } from "@testing-library/react";
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
import { FULL_TEXT_SECTION, type GraphResponse, type Idea, type IdeaPatch } from "@/types/ideation";

import { IdeaDetail } from "./IdeaDetail";

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

const server = setupServer(
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
afterEach(() => server.resetHandlers());

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
  toast.mockClear();
  scrollIntoView.mockClear();
  toast.dismiss.mockClear();
  useNavStore.setState({ page: "ideas" });
  useChatStore.setState({ sessions: [], activeSessionId: null, composerDraft: "" });
});

describe("IdeaDetail", () => {
  it("autosaves the note with one PATCH once typing stops", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByRole("textbox", { name: "Note" }), "Run it on the eval set");
    expect(screen.getByTestId("note-state")).toHaveTextContent("Saving…");
    await waitFor(() => expect(screen.getByTestId("note-state")).toHaveTextContent("Saved"), { timeout: 2000 });
    expect(patches).toEqual([{ note: "Run it on the eval set" }]);
  });

  it("flushes a pending note when the drawer switches to another idea", async () => {
    const user = userEvent.setup();
    setup();
    useIdeationStore.setState((s) => ({
      graph: s.graph && { ...s.graph, ideas: [...s.graph.ideas, base({ id: 6, title: "Other idea" })] },
    }));
    await user.type(screen.getByRole("textbox", { name: "Note" }), "half a thought");
    act(() => useIdeationStore.setState({ selected: { kind: "idea", id: 6 } }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ note: "half a thought" });
    expect(patchedIds).toEqual([5]);
    await new Promise((r) => setTimeout(r, 700));
    expect(patches).toHaveLength(1);
  });

  it("doesn't say Saved while a newer edit is still pending", async () => {
    const user = userEvent.setup();
    patchDelayMs = 400;
    setup();
    const box = screen.getByRole("textbox", { name: "Note" });
    await user.type(box, "first");
    // The first save is in flight; type more before it returns.
    await waitFor(() => expect(patches).toHaveLength(1), { timeout: 1500 });
    await user.type(box, " second");
    await new Promise((r) => setTimeout(r, 450));
    expect(screen.getByTestId("note-state")).toHaveTextContent("Saving…");
    await waitFor(() => expect(screen.getByTestId("note-state")).toHaveTextContent("Saved"), { timeout: 3000 });
    expect(patches.at(-1)).toEqual({ note: "first second" });
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
