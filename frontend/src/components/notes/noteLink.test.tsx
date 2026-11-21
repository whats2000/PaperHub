import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

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
import type { NoteBlock } from "@/types/notes";

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

  it("a click checks an old lookup again: a note deleted since shows dead and opens nothing", async () => {
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
    expect(toastFn).not.toHaveBeenCalled();
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

  it("clicking a chip whose place is gone says so, neutrally", async () => {
    server.use(noteOf(21, "session", "77"), http.get(`${API_BASE_URL}/sessions`, () => HttpResponse.json([])));
    useChatStore.setState({ sessions: [], activeSessionId: null });
    seed([link({ targetId: 21, name: "Chat note" })]);
    mount();
    await waitFor(() => expect(chip()?.textContent).toBe("N"));
    fireEvent.click(chip()!);
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith("Couldn't open that link: where it points is not there."));
    expect(errorFn).not.toHaveBeenCalled();
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
