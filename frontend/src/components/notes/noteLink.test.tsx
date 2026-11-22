import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { resetLinkPicker } from "@/components/notes/linkPicker";
import { NoteEditor } from "@/components/notes/NoteEditor";
import { findMatches } from "@/components/notes/noteFind";
import { cleanupEditors, makeEditor } from "@/components/notes/testEditor";
import { API_BASE_URL } from "@/lib/api";
import { openNoteLink } from "@/lib/notes/openNoteLink";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useIdeationStore } from "@/store/ideation";
import { useNavStore } from "@/store/nav";
import { useNotesPaneStore } from "@/store/notesPane";
import { clearPendingNoteOps, pendingNoteOps, resetLinkTargets, useNotesStore } from "@/store/notes";
import type { NoteBlock, NoteOpsBody } from "@/types/notes";

const { toastFn, errorFn } = vi.hoisted(() => ({ toastFn: vi.fn(), errorFn: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastFn, { error: errorFn, success: vi.fn() }) }));

const server = setupServer();

function blk(id: string, extra: Partial<NoteBlock>): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: "paragraph",
    props: {},
    content: null,
    text: "",
    author: "user",
    section_key: null,
    changed_rev: 1,
    ...extra,
  };
}

interface LinkProps {
  targetKind: "note" | "paper";
  targetId: number;
  blockId: string;
  sectionKey: string;
  label: string;
  name: string;
}

const link = (over: Partial<LinkProps> = {}) => ({
  type: "noteLink",
  props: { targetKind: "note", targetId: 12, blockId: "", sectionKey: "", label: "", name: "Old name", ...over },
});

function seed(content: unknown[]) {
  useNotesStore.setState({
    notes: {
      7: {
        note: { id: 7, target_kind: "paper", target_id: "3", title: "Mine", revision: 4, updated_at: "" },
        blocks: [blk("p1", { content: [{ type: "text", text: "see ", styles: {} }, ...content] as never, text: "see" })],
        suggestions: [],
        comments: [],
        revision: 4,
      },
    },
    byTarget: { "paper:3": 7 },
    reloads: {},
  });
}

/** The target note 12: a paper note (paper 47) with a heading h1. */
function note12(status = 200) {
  server.use(
    http.get(`${API_BASE_URL}/notes/12`, () =>
      status !== 200
        ? new HttpResponse(null, { status })
        : HttpResponse.json({
            note: { id: 12, target_kind: "paper", target_id: "47", title: "", revision: 1, updated_at: "" },
            default_title: "RoboTTT",
            shared_by: 0,
            suggestions: [],
            comments: [],
            blocks: [
              blk("h0", { note_id: 12, type: "paragraph", text: "First line of the note" }),
              blk("h1", { note_id: 12, type: "heading", props: { level: 2 }, text: "閘控" }),
              blk("h2", { note_id: 12, type: "paragraph", text: "Under the heading" }),
            ],
          }),
    ),
    // The paper of note 12, named in the preview's kind line.
    http.get(`${API_BASE_URL}/papers/content/47/info`, () =>
      HttpResponse.json({ id: 47, title: "RoboTTT: Context Scaling for Robot Policies" }),
    ),
  );
}

const paperInfo = (id: number, title: string) =>
  http.get(`${API_BASE_URL}/papers/content/${id}/info`, () => HttpResponse.json({ id, title }));
const sectionRange = (id: number, section: string, first: number, last: number) =>
  http.get(`${API_BASE_URL}/papers/content/${id}/section-range`, ({ request }) =>
    new URL(request.url).searchParams.get("section") === section
      ? HttpResponse.json({ first_chunk_id: first, last_chunk_id: last })
      : new HttpResponse(null, { status: 404 }),
  );

function mount() {
  act(() => {
    render(<NoteEditor noteId={7} variant="pane" />);
  });
}

const chip = () => document.querySelector<HTMLElement>("[data-note-link-chip]");
const hoverPreview = async () => {
  fireEvent.mouseEnter(chip()!);
  return screen.findByTestId("note-link-preview");
};

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  resetLinkPicker();
  clearPendingNoteOps();
  server.resetHandlers();
  toastFn.mockReset();
  errorFn.mockReset();
  cleanupEditors();
});
beforeEach(() => {
  useNotesStore.setState({ colorNames: {}, retrying: {}, drafts: {}, folded: {}, focusOn: false, activePart: {} });
  useNotesPaneStore.setState({ open: false, request: null });
  resetLinkTargets();
  server.use(http.get(`${API_BASE_URL}/marks/colors`, () => HttpResponse.json([])));
});

describe("a [[ link chip", () => {
  it("shows the target's current name, underlined in the text colour (never citation yellow)", async () => {
    note12();
    seed([link()]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT"));
    expect(chip()!.className).toMatch(/underline/);
    expect(chip()!.className).toMatch(/text-foreground/);
    expect(chip()!.className).not.toMatch(/yellow/);
    expect(chip()!.querySelector("svg")).not.toBeNull();
  });

  it("flows inline in the sentence: a long name wraps inside the chip, never on a line of its own", async () => {
    note12();
    seed([link()]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT"));
    // An inline-flex / inline-block box is atomic: a name wider than the
    // rest of the line drops whole onto the next line. Plain inline boxes
    // break inside the name, like a Notion mention.
    const boxes = [chip()!, chip()!.closest("[data-note-link]")!];
    for (const el of boxes) expect(el.className).not.toMatch(/\binline-(flex|block)\b/);
    expect(chip()!.className).toMatch(/box-decoration-clone/);
  });

  it("adds › heading for a heading link, and a label replaces both", async () => {
    note12();
    seed([link({ blockId: "h1" }), { type: "text", text: " and ", styles: {} }, link({ label: "見此", blockId: "h1" })]);
    mount();
    await waitFor(() => {
      const chips = [...document.querySelectorAll("[data-note-link-chip]")].map((c) => c.textContent);
      expect(chips).toEqual(["RoboTTT › 閘控", "見此"]);
    });
  });

  it("shows the name saved at insert time until the target is looked up", async () => {
    server.use(http.get(`${API_BASE_URL}/notes/12`, () => new Promise(() => undefined)));
    seed([link()]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("Old name"));
  });

  it("previews the path and first lines on hover, with Open and Edit display text", async () => {
    note12();
    seed([link({ blockId: "h1" })]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT › 閘控"));
    const preview = await hoverPreview();
    expect(preview.textContent).toContain("RoboTTT › 閘控");
    expect(preview.textContent).toContain("Under the heading");
    expect(within(preview).getByRole("button", { name: "Open" })).toBeInTheDocument();
    expect(within(preview).getByRole("button", { name: "Edit display text" })).toBeInTheDocument();
  });

  it("previews as a card: kind and source, the target in bold, a clamped paragraph, two buttons", async () => {
    note12();
    seed([link({ blockId: "h1" })]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT › 閘控"));
    const preview = await hoverPreview();
    // The kind, then where it lives: a paper note names its paper.
    const kind = preview.querySelector("[data-preview-kind]")!;
    expect(kind.textContent).toContain("Paper note");
    await waitFor(() => expect(kind.textContent).toContain("RoboTTT: Context Scaling for Robot Policies"));
    expect(kind.querySelector("svg")).not.toBeNull();
    const title = preview.querySelector("[data-preview-title]")!;
    expect(title.textContent).toBe("RoboTTT › 閘控");
    expect(title.className).toMatch(/font-semibold/);
    // One wrapped paragraph cut after three lines, not a line per block.
    const body = preview.querySelectorAll("[data-preview-body]");
    expect(body).toHaveLength(1);
    expect(body[0]!.className).toMatch(/line-clamp-3/);
    expect(body[0]!.textContent).toContain("Under the heading");
    // Two labelled buttons in a row under it.
    const buttons = within(preview.querySelector("[data-preview-actions]") as HTMLElement).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Open", "Edit display text"]);
  });

  it("draws inline math in the preview's lines as the editor does, never as raw LaTeX", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/12`, () =>
        HttpResponse.json({
          note: { id: 12, target_kind: "paper", target_id: "47", title: "", revision: 1, updated_at: "" },
          default_title: "LoRA",
          shared_by: 0,
          suggestions: [],
          comments: [],
          blocks: [
            blk("m1", {
              note_id: 12,
              content: [
                { type: "text", text: "The update ", styles: {} },
                { type: "inlineMath", props: { latex: "\\Delta W = BA" } },
                { type: "text", text: " is low rank", styles: { bold: true } },
              ] as never,
              text: "The update \\Delta W = BA is low rank",
            }),
          ],
        }),
      ),
    );
    seed([link()]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("LoRA"));
    const preview = await hoverPreview();
    expect(preview.querySelector(".katex")).not.toBeNull();
    expect(preview.textContent).toContain("The update ");
    expect(preview.textContent).toContain(" is low rank");
    expect(preview.textContent).not.toContain("\\Delta W = BA is");
  });

  it("Edit display text sets the link's label", async () => {
    note12();
    seed([link()]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT"));
    const preview = await hoverPreview();
    fireEvent.click(within(preview).getByRole("button", { name: "Edit display text" }));
    const field = screen.getByRole<HTMLInputElement>("textbox", { name: "Display text" });
    await waitFor(() => expect(document.activeElement).toBe(field));
    fireEvent.change(field, { target: { value: "see this" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(chip()!.textContent).toBe("see this"));
    await waitFor(() => {
      const sent = pendingNoteOps(7)
        .flatMap((o) => (Array.isArray(o.block?.content) ? o.block.content : []))
        .find((r) => (r as { type?: string }).type === "noteLink") as { props: Record<string, unknown> } | undefined;
      expect(sent?.props).toMatchObject({ targetId: 12, label: "see this" });
    });
  });

  it("a deleted target shows grey and struck through, its preview says so, and no toast", async () => {
    note12(404);
    seed([link()]);
    mount();
    await waitFor(() => expect(chip()).toHaveAttribute("data-dead", "true"));
    expect(chip()!.textContent).toBe("Old name");
    expect(chip()!.className).toMatch(/line-through/);
    expect(chip()!.className).toMatch(/text-muted-foreground/);
    const preview = await hoverPreview();
    expect(preview.textContent).toContain("This note was deleted");
    expect(within(preview).queryByRole("button", { name: "Open" })).toBeNull();
    const before = useNotesPaneStore.getState().request;
    fireEvent.click(chip()!);
    expect(useNotesPaneStore.getState().request).toBe(before);
    expect(toastFn).not.toHaveBeenCalled();
    expect(errorFn).not.toHaveBeenCalled();
  });

  it("a deleted heading is dead too", async () => {
    note12();
    seed([link({ blockId: "gone" })]);
    mount();
    await waitFor(() => expect(chip()).toHaveAttribute("data-dead", "true"));
    const preview = await hoverPreview();
    expect(preview.textContent).toContain("This heading was deleted");
  });

  it("a paper link follows the paper's current title and opens the paper in the reader", async () => {
    server.use(paperInfo(5, "Attention Is All You Need"));
    seed([link({ targetKind: "paper", targetId: 5, name: "Attention" })]);
    useCanvasStore.setState({ requestedPaperId: null });
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("Attention Is All You Need"));
    fireEvent.click(chip()!);
    await waitFor(() => expect(useCanvasStore.getState().requestedPaperId).toBe(5));
  });

  it("a paper section link opens the reader at that section", async () => {
    server.use(paperInfo(5, "Attention"), sectionRange(5, "2 Method", 403, 405));
    seed([link({ targetKind: "paper", targetId: 5, sectionKey: "2 Method", name: "Attention › 2 Method" })]);
    useCanvasStore.setState({ requestedChunkId: null, requestedEndChunkId: null });
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("Attention › 2 Method"));
    fireEvent.click(chip()!);
    await waitFor(() => expect(useCanvasStore.getState().requestedChunkId).toBe(403));
    expect(useCanvasStore.getState().requestedEndChunkId).toBe(405);
  });

  it("an @ section mention opens the reader at that section too", async () => {
    server.use(sectionRange(5, "2 Method", 403, 405));
    seed([{ type: "mention", props: { kind: "section", paperId: 5, sectionKey: "2 Method", label: "§2 Method" } }]);
    useCanvasStore.setState({ requestedChunkId: null, requestedEndChunkId: null });
    mount();
    const mention = await screen.findByRole("button", { name: "§2 Method" });
    fireEvent.click(mention);
    await waitFor(() => expect(useCanvasStore.getState().requestedChunkId).toBe(403));
    expect(useCanvasStore.getState().requestedEndChunkId).toBe(405);
  });

  it("a click checks an old lookup again: a note deleted since shows dead, says so and opens nothing", async () => {
    note12(404);
    seed([link()]);
    useNotesStore.setState({
      linkTargets: {
        "note:12": { status: "ok", checkedAt: Date.now() - 61_000, noteKind: "paper", targetId: 47, name: "RoboTTT", blocks: [] },
      },
    });
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT"));
    fireEvent.click(chip()!);
    await waitFor(() => expect(chip()).toHaveAttribute("data-dead", "true"));
    expect(useNotesPaneStore.getState().request).toBeNull();
    // A plain notice says which case it is (never an error toast); the
    // chip stays grey in place.
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith("This note was deleted"));
    expect(errorFn).not.toHaveBeenCalled();
  });

  it("a click on a dead chip checks again first: a heading added since opens", async () => {
    note12();
    seed([link({ blockId: "h1" })]);
    useNotesStore.setState({
      linkTargets: {
        "note:12": { status: "ok", checkedAt: Date.now(), noteKind: "paper", targetId: 47, name: "RoboTTT", blocks: [] },
      },
    });
    mount();
    await waitFor(() => expect(chip()).toHaveAttribute("data-dead", "true"));
    fireEvent.click(chip()!);
    await waitFor(() => expect(chip()).not.toHaveAttribute("data-dead", "true"));
    expect(chip()!.textContent).toBe("RoboTTT › 閘控");
    await waitFor(() =>
      expect(useNotesPaneStore.getState().request).toMatchObject({ noteId: 12, blockId: "h1" }),
    );
    expect(toastFn).not.toHaveBeenCalled();
  });

  it("a dead verdict a few seconds old is checked again when drawn: a restored note shows live", async () => {
    note12();
    seed([link()]);
    useNotesStore.setState({ linkTargets: { "note:12": { status: "dead", checkedAt: Date.now() - 30_000 } } });
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT"));
    expect(chip()).not.toHaveAttribute("data-dead", "true");
  });

  it("hovering a dead paper chip checks it again even when a note with the same id is loaded", async () => {
    server.use(http.get(`${API_BASE_URL}/papers/content/7/info`, () => new HttpResponse(null, { status: 404 })));
    seed([link({ targetKind: "paper", targetId: 7 })]);
    useNotesStore.setState({ linkTargets: { "paper:7": { status: "dead", checkedAt: Date.now() } } });
    mount();
    await waitFor(() => expect(chip()).toHaveAttribute("data-dead", "true"));
    // The verdict ages past the re-check window; note 7 (the open note) is loaded.
    useNotesStore.setState({ linkTargets: { "paper:7": { status: "dead", checkedAt: Date.now() - 60_000 } } });
    const spy = vi.spyOn(useNotesStore.getState(), "resolveLinkTarget");
    fireEvent.mouseEnter(chip()!);
    expect(spy).toHaveBeenCalledWith("paper", 7, expect.anything());
    spy.mockRestore();
  });

  it("an agent change to the target note refreshes its looked-up outline", async () => {
    note12();
    seed([link({ blockId: "h1" })]);
    useNotesStore.setState({
      linkTargets: {
        "note:12": { status: "ok", checkedAt: Date.now(), noteKind: "paper", targetId: 47, name: "RoboTTT", blocks: [] },
      },
    });
    useChatStore.setState({ sessions: [{ id: 1, backend_session_id: 9, title: "c", messages: [] }] });
    mount();
    await waitFor(() => expect(chip()).toHaveAttribute("data-dead", "true"));
    act(() => useChatStore.getState().applyAgentEvent(1, 5, "notes_changed", { note_id: 12 }));
    await waitFor(() => expect(chip()).not.toHaveAttribute("data-dead", "true"));
    expect(chip()!.textContent).toBe("RoboTTT › 閘控");
  });

  it("Edit display text starts empty, never from the placeholder", async () => {
    server.use(http.get(`${API_BASE_URL}/notes/12`, () => new Promise(() => undefined)));
    seed([link({ name: "" })]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("…"));
    const preview = await hoverPreview();
    fireEvent.click(within(preview).getByRole("button", { name: "Edit display text" }));
    expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Display text" }).value).toBe("");
  });

  it("is reachable by keyboard: focus shows the preview, Tab moves into it, Enter follows the link", async () => {
    note12();
    seed([link({ blockId: "h1" })]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT › 閘控"));
    expect(chip()).toHaveAttribute("tabindex", "0");
    act(() => chip()!.focus());
    const preview = await screen.findByTestId("note-link-preview");
    fireEvent.keyDown(chip()!, { key: "Tab" });
    expect(document.activeElement).toBe(within(preview).getByRole("button", { name: "Open" }));
    act(() => chip()!.focus());
    fireEvent.keyDown(chip()!, { key: "Enter" });
    await waitFor(() =>
      expect(useNotesPaneStore.getState().request).toMatchObject({ kind: "paper", id: 47, noteId: 12, blockId: "h1" }),
    );
  });

  it("clicking a link to a heading of a paper's note opens that note at the heading", async () => {
    note12();
    seed([link({ blockId: "h1" })]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("RoboTTT › 閘控"));
    useNavStore.setState({ page: "ideas" });
    fireEvent.click(chip()!);
    await waitFor(() =>
      expect(useNotesPaneStore.getState().request).toMatchObject({ kind: "paper", id: 47, noteId: 12, blockId: "h1" }),
    );
    expect(useNavStore.getState().page).toBe("chat");
  });
});

describe("following a link to another place", () => {
  const noteOf = (id: number, kind: string, target: string) =>
    http.get(`${API_BASE_URL}/notes/${id}`, () =>
      HttpResponse.json({
        note: { id, target_kind: kind, target_id: target, title: "N", revision: 1, updated_at: "" },
        default_title: "",
        shared_by: 0,
        suggestions: [],
        comments: [],
        blocks: [blk("b9", { note_id: id, type: "heading", text: "H" })],
      }),
    );

  it("a chat's note switches to that chat first, then opens its note", async () => {
    server.use(noteOf(20, "session", "43"));
    const chat = (id: number, backend: number) => ({ id, title: `c${id}`, messages: [], backend_session_id: backend });
    useChatStore.setState({ sessions: [chat(1, 42), chat(2, 43)], activeSessionId: 1 });
    await expect(openNoteLink({ targetKind: "note", targetId: 20, blockId: "b9", sectionKey: "" })).resolves.toBe("opened");
    expect(useChatStore.getState().activeSessionId).toBe(2);
    expect(useNotesPaneStore.getState().request).toMatchObject({ kind: "session", id: 43, noteId: 20, blockId: "b9" });
  });

  it("an idea's note opens the idea on the Ideas page, in the scope that holds it", async () => {
    const graph = (ideas: { id: number }[]) => ({
      documents: [], domains: [], citations: [], ideas, problem: null, library_count: 0, private_count: 0,
    });
    server.use(
      noteOf(30, "idea", "9"),
      http.get(`${API_BASE_URL}/ideation/graph`, ({ request }) =>
        HttpResponse.json(graph(new URL(request.url).searchParams.get("explore") ? [{ id: 9 }] : [])),
      ),
    );
    useIdeationStore.setState({ scope: 5, graph: graph([]) as never, problems: [{ id: 5 }] as never, selected: null });
    useNavStore.setState({ page: "chat" });
    await expect(openNoteLink({ targetKind: "note", targetId: 30, blockId: "", sectionKey: "" })).resolves.toBe("opened");
    expect(useNavStore.getState().page).toBe("ideas");
    expect(useIdeationStore.getState().scope).toBe("explore");
    expect(useIdeationStore.getState().selected).toEqual({ kind: "idea", id: 9 });
  });

  it("a chat missing from the list is synced once, then reported, without leaving the page", async () => {
    server.use(noteOf(21, "session", "77"), http.get(`${API_BASE_URL}/sessions`, () => HttpResponse.json([])));
    useChatStore.setState({ sessions: [], activeSessionId: null });
    useNavStore.setState({ page: "ideas" });
    await expect(openNoteLink({ targetKind: "note", targetId: 21, blockId: "", sectionKey: "" })).resolves.toBe(
      "unreachable",
    );
    expect(useNavStore.getState().page).toBe("ideas");
    expect(useNotesPaneStore.getState().request).toBeNull();
  });

  it("a chat that arrives with the sync opens", async () => {
    const summary = { id: 78, title: "Later", created_at: "", updated_at: "", message_count: 0 };
    server.use(noteOf(22, "session", "78"), http.get(`${API_BASE_URL}/sessions`, () => HttpResponse.json([summary])));
    useChatStore.setState({ sessions: [], activeSessionId: null });
    await expect(openNoteLink({ targetKind: "note", targetId: 22, blockId: "", sectionKey: "" })).resolves.toBe("opened");
    expect(useNotesPaneStore.getState().request).toMatchObject({ kind: "session", id: 78, noteId: 22 });
  });

  it("an idea in no scope leaves the page as it is", async () => {
    server.use(
      noteOf(32, "idea", "99"),
      http.get(`${API_BASE_URL}/ideation/graph`, () =>
        HttpResponse.json({ documents: [], domains: [], citations: [], ideas: [], problem: null, library_count: 0, private_count: 0 }),
      ),
      http.get(`${API_BASE_URL}/ideation/problems`, () => HttpResponse.json([])),
    );
    useIdeationStore.setState({ scope: "explore", graph: null, problems: [] as never });
    useNavStore.setState({ page: "chat" });
    await expect(openNoteLink({ targetKind: "note", targetId: 32, blockId: "", sectionKey: "" })).resolves.toBe(
      "unreachable",
    );
    expect(useNavStore.getState().page).toBe("chat");
  });

  it("clicking a chip it can't open now says so, neutrally, with Retry", async () => {
    let sessions: unknown[] = [];
    server.use(noteOf(21, "session", "77"), http.get(`${API_BASE_URL}/sessions`, () => HttpResponse.json(sessions)));
    useChatStore.setState({ sessions: [], activeSessionId: null });
    seed([link({ targetId: 21, name: "Chat note" })]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("N"));
    fireEvent.click(chip()!);
    await waitFor(() => expect(toastFn).toHaveBeenCalledTimes(1));
    const [text, opts] = toastFn.mock.calls[0] as [string, { action: { label: string; onClick: () => void } }];
    expect(text).toBe("Can't open this right now");
    expect(opts.action.label).toBe("Retry");
    expect(errorFn).not.toHaveBeenCalled();
    // The chip is not dead: only out of reach. Retry, once it is there, opens it.
    expect(chip()).not.toHaveAttribute("data-dead");
    sessions = [{ id: 77, title: "Back", created_at: "", updated_at: "", message_count: 1 }];
    opts.action.onClick();
    await waitFor(() =>
      expect(useNotesPaneStore.getState().request).toMatchObject({ kind: "session", id: 77, noteId: 21 }),
    );
    expect(toastFn).toHaveBeenCalledTimes(1);
  });

  it("a deleted note opens nothing", async () => {
    server.use(http.get(`${API_BASE_URL}/notes/31`, () => new HttpResponse(null, { status: 404 })));
    await expect(openNoteLink({ targetKind: "note", targetId: 31, blockId: "", sectionKey: "" })).resolves.toBe("gone");
    expect(useNotesPaneStore.getState().request).toBeNull();
  });
});

describe("find in a note with links", () => {
  it("matches a link's label (or its name) as one unit", () => {
    const editor = makeEditor([], [
      {
        id: "p",
        type: "paragraph",
        content: [
          { type: "text", text: "go ", styles: {} },
          { type: "noteLink", props: { targetKind: "note", targetId: 1, blockId: "", sectionKey: "", label: "see this", name: "X" } },
          { type: "noteLink", props: { targetKind: "paper", targetId: 2, blockId: "", sectionKey: "", label: "", name: "Attention" } },
        ],
      },
    ]);
    const doc = editor.prosemirrorState.doc;
    expect(findMatches(doc, "this").map((m) => m.kind)).toEqual(["node"]);
    expect(findMatches(doc, "atten").map((m) => m.kind)).toEqual(["node"]);
    expect(findMatches(doc, "X")).toEqual([]);
  });
});

describe("the [[ picker", () => {
  const rect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect;
  type PMView = {
    state: { selection: { from: number; to: number }; tr: { insertText: (t: string, f: number, to: number) => unknown }; doc: { content: { size: number } } };
    someProp: (name: string, f: (fn: (...a: unknown[]) => boolean) => boolean) => boolean;
    dispatch: (tr: unknown) => void;
  };
  const view = () =>
    (document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { view: PMView; commands: { setTextSelection: (pos: number) => boolean; focus: () => boolean } };
    }).editor;
  const typeIn = (text: string) =>
    act(() => {
      const v = view().view;
      for (const ch of text) {
        const { from, to } = v.state.selection;
        const handled = v.someProp("handleTextInput", (f) => f(v, from, to, ch, () => v.state.tr));
        if (!handled) v.dispatch(v.state.tr.insertText(ch, from, to));
      }
    });
  const lineText = () => document.querySelector('[data-id="p1"] .bn-inline-content')?.textContent ?? "";
  const menuItems = () =>
    [...document.querySelectorAll(".bn-suggestion-menu-item")].filter(
      (e) => !e.parentElement?.closest(".bn-suggestion-menu-item"),
    );

  // On a line inside the editor: the menu listens in the capture phase. The
  // menu draws its rows before a passive effect hands its key listener those
  // rows; until then Enter goes to the listener holding the old (empty)
  // rows and picks nothing. Flush the effects first: a key pressed between
  // the two was the noteLink tests' flake under a loaded full-suite run.
  const pressInMenu = async (key: string) => {
    await act(async () => {});
    fireEvent.keyDown(document.querySelector(".ProseMirror .bn-inline-content")!, { key });
  };

  async function openAtEnd() {
    posted.length = 0;
    server.use(opsHandler());
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(rect);
    if (!("getBoundingClientRect" in Range.prototype)) {
      Object.defineProperty(Range.prototype, "getBoundingClientRect", { configurable: true, value: rect });
      Object.defineProperty(Range.prototype, "getClientRects", { configurable: true, value: () => [] });
    }
    seed([]);
    mount();
    await screen.findByTestId("note-editor");
    act(() => {
      view().commands.focus();
      view().commands.setTextSelection(view().view.state.doc.content.size - 3);
    });
    // tiptap focuses the DOM a frame later; that focus would close a menu
    // opened before it (a person is already in the line when typing).
    await waitFor(() => expect(document.activeElement).toBe(document.querySelector(".ProseMirror")));
  }

  const searchHandlers = () => [
    http.get(`${API_BASE_URL}/notes/search`, () =>
      HttpResponse.json([
        { note_id: 12, kind: "paper", target_id: 47, title: "RoboTTT" },
        { note_id: 13, kind: "session", target_id: 43, title: "Robot chat" },
        { note_id: 14, kind: "idea", target_id: 9, title: "Robot idea" },
      ]),
    ),
    http.get(`${API_BASE_URL}/papers/library`, () =>
      HttpResponse.json([{ paper_content_id: 5, arxiv_id: null, title: "Robots at scale", abstract: null, year: 2025, is_private: false }]),
    ),
  ];

  const posted: NoteOpsBody[] = [];
  const opsHandler = () =>
    http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
      const body = (await request.json()) as NoteOpsBody;
      posted.push(body);
      return HttpResponse.json({
        note_id: 7,
        revision: 5 + posted.length,
        change_set_id: `cs_${posted.length}`,
        applied: body.ops.map((o) => o.block_id),
        suggested: [],
        rejected: [],
        reparented: [],
      });
    });
  const allOps = () => [...posted.flatMap((b) => b.ops), ...pendingNoteOps(7)];
  const sentLink = () =>
    allOps()
      .flatMap((o) => (Array.isArray(o.block?.content) ? o.block.content : []))
      .filter((r) => (r as { type?: string }).type === "noteLink") as { props: Record<string, unknown> }[];

  it("[[ opens a menu of notes (an icon per kind) and papers, with no create item", async () => {
    server.use(...searchHandlers());
    await openAtEnd();
    typeIn("[[");
    await screen.findByText("Robot idea");
    expect(screen.getByText("Notes")).toBeInTheDocument();
    expect(screen.getByText("Papers")).toBeInTheDocument();
    const icon = (title: string) =>
      menuItems().find((e) => e.textContent?.includes(title))!.querySelector("svg")!.getAttribute("class");
    expect(icon("RoboTTT")).toMatch(/notebook-pen/);
    expect(icon("Robot chat")).toMatch(/message-square/);
    expect(icon("Robot idea")).toMatch(/lightbulb/);
    expect(icon("Robots at scale")).toMatch(/file-text/);
    expect(menuItems().map((e) => e.textContent)).not.toContainEqual(expect.stringMatching(/create|new note/i));
  });

  it("[[ rows are compact: small group labels, a title line and a muted type line", async () => {
    server.use(...searchHandlers());
    await openAtEnd();
    typeIn("[[");
    await screen.findByText("Robot idea");
    const menu = document.querySelector(".bn-suggestion-menu")!;
    expect(menu).toHaveAttribute("data-link-picker");
    expect([...menu.querySelectorAll("[data-link-picker-group]")].map((e) => e.textContent)).toEqual(["Notes", "Papers"]);
    const row = (title: string) => menuItems().find((e) => e.textContent?.includes(title))!;
    const parts = (title: string) => [
      row(title).querySelector("[data-link-row-title]")?.textContent,
      row(title).querySelector("[data-link-row-type]")?.textContent,
    ];
    expect(parts("RoboTTT")).toEqual(["RoboTTT", "Paper note"]);
    expect(parts("Robot chat")).toEqual(["Robot chat", "Chat note"]);
    expect(parts("Robot idea")).toEqual(["Robot idea", "Idea note"]);
    expect(parts("Robots at scale")).toEqual(["Robots at scale", "Paper · 2025"]);
    // One line each: a long title ends in an ellipsis instead of wrapping.
    expect(row("RoboTTT").querySelector("[data-link-row-title]")!.className).toMatch(/truncate/);
  });

  it("Enter inserts a link with a trailing space, and |text sets its label", async () => {
    server.use(...searchHandlers());
    await openAtEnd();
    typeIn("[[robo|見此");
    await screen.findByText("Robot idea");
    await pressInMenu("Enter");
    await waitFor(() => expect(sentLink()).toHaveLength(1));
    expect(sentLink()[0]!.props).toEqual({
      targetKind: "note",
      targetId: 12,
      blockId: "",
      sectionKey: "",
      label: "見此",
      name: "RoboTTT",
    });
    const content = allOps().at(-1)!.block!.content as { type: string; text?: string }[];
    expect(content.map((r) => r.type)).toEqual(["text", "noteLink", "text"]);
    expect(content[0]!.text).toBe("see ");
    expect(content[2]!.text).toBe(" ");
  });

  it("a third [ stays in the query: the pick leaves no stray bracket", async () => {
    server.use(...searchHandlers());
    await openAtEnd();
    typeIn("[[[robo");
    await screen.findByText("Robot idea");
    await pressInMenu("Enter");
    await waitFor(() => expect(sentLink()).toHaveLength(1));
    const content = allOps().at(-1)!.block!.content as { type: string; text?: string }[];
    expect(content[0]!.text).toBe("see ");
    expect(content[1]!.type).toBe("noteLink");
  });

  it("an empty display text after | leaves the label empty", async () => {
    server.use(...searchHandlers());
    await openAtEnd();
    typeIn("[[robo|");
    await screen.findByText("Robot idea");
    await pressInMenu("Enter");
    await waitFor(() => expect(sentLink()).toHaveLength(1));
    expect(sentLink()[0]!.props.label).toBe("");
  });

  it("[[name# lists that note's outline and links the heading", async () => {
    server.use(
      ...searchHandlers(),
      http.get(`${API_BASE_URL}/notes/12/outline`, () =>
        HttpResponse.json([{ block_id: "h1", type: "heading", text: "閘控", level: 2 }]),
      ),
    );
    await openAtEnd();
    typeIn("[[RoboTTT#");
    await screen.findByText("閘控");
    expect(screen.getByText("Headings")).toBeInTheDocument();
    // Only exact titles list headings: "Robots at scale" adds no sections.
    expect(screen.queryByText("Paper sections")).toBeNull();
    await pressInMenu("Enter");
    await waitFor(() => expect(sentLink()).toHaveLength(1));
    expect(sentLink()[0]!.props).toMatchObject({ targetId: 12, blockId: "h1", name: "RoboTTT › 閘控" });
  });

  it("a paper note and its paper of the same title are two rows", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, () =>
        HttpResponse.json([{ note_id: 12, kind: "paper", target_id: 5, title: "Attention" }]),
      ),
      http.get(`${API_BASE_URL}/papers/library`, () =>
        HttpResponse.json([{ paper_content_id: 5, arxiv_id: null, title: "Attention", abstract: null, year: 2017, is_private: false }]),
      ),
    );
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await openAtEnd();
    typeIn("[[att");
    await waitFor(() => expect(menuItems()).toHaveLength(2));
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();
    await pressInMenu("ArrowDown");
    await pressInMenu("Enter");
    await waitFor(() => expect(sentLink()).toHaveLength(1));
    expect(sentLink()[0]!.props).toMatchObject({ targetKind: "paper", targetId: 5, name: "Attention" });
  });

  it("[[Robo# lists RoboTTT; picking it shows its outline; a heading then links", async () => {
    const like = (q: string, title: string) => title.toLowerCase().includes(q.toLowerCase());
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, ({ request }) => {
        const q = new URL(request.url).searchParams.get("q") ?? "";
        const all = [{ note_id: 12, kind: "paper", target_id: 47, title: "RoboTTT" }];
        return HttpResponse.json(all.filter((n) => like(q, n.title)));
      }),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/notes/12/outline`, () =>
        HttpResponse.json([{ block_id: "h1", type: "heading", text: "閘控", level: 2 }]),
      ),
    );
    await openAtEnd();
    typeIn("[[Robo#");
    await screen.findByText("RoboTTT");
    await pressInMenu("Enter");
    await screen.findByText("閘控");
    expect(lineText()).toBe("see [[RoboTTT#");
    expect(sentLink()).toHaveLength(0);
    await pressInMenu("Enter");
    await waitFor(() => expect(sentLink()).toHaveLength(1));
    expect(sentLink()[0]!.props).toMatchObject({ targetId: 12, blockId: "h1", name: "RoboTTT › 閘控" });
    const content = allOps().at(-1)!.block!.content as { type: string; text?: string }[];
    expect(content[0]!.text).toBe("see ");
  });

  it("nothing matching shows a muted hint row, which links nothing", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([])),
    );
    await openAtEnd();
    typeIn("[[zzzzzz");
    await screen.findByText("No matching notes or papers");
    await pressInMenu("Enter");
    await screen.findByText("No matching notes or papers");
    expect(lineText()).toBe("see [[zzzzzz");
    expect(sentLink()).toHaveLength(0);
  });

  it("Escape closes the menu and leaves what was typed", async () => {
    server.use(...searchHandlers());
    await openAtEnd();
    typeIn("[[robo");
    await screen.findByText("Robot idea");
    fireEvent.keyDown(document.querySelector(".ProseMirror")!, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText("Robot idea")).toBeNull());
    expect(lineText()).toBe("see [[robo");
    expect(sentLink()).toHaveLength(0);
  });
});
