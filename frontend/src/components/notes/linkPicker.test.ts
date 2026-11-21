import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { linkItems, parseLinkQuery } from "@/components/notes/linkPicker";
import { cleanupEditors, makeEditor } from "@/components/notes/testEditor";
import { API_BASE_URL } from "@/lib/api";
import { useNotesStore } from "@/store/notes";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  server.resetHandlers();
  cleanupEditors();
});

describe("parseLinkQuery", () => {
  it("reads a name as a search for notes and papers", () => {
    expect(parseLinkQuery("robo")).toEqual({ mode: "targets", name: "robo", heading: "", label: "" });
  });
  it("reads name#word as that note's headings", () => {
    expect(parseLinkQuery("robo#閘")).toEqual({ mode: "noteHeadings", name: "robo", heading: "閘", label: "" });
  });
  it("reads #word as this note's headings", () => {
    expect(parseLinkQuery("#動")).toEqual({ mode: "thisNote", name: "", heading: "動", label: "" });
  });
  it("reads ##word as headings across all notes", () => {
    expect(parseLinkQuery("##閘")).toEqual({ mode: "allHeadings", name: "", heading: "閘", label: "" });
  });
  it("reads |text as the display text", () => {
    expect(parseLinkQuery("robo|見此")).toEqual({ mode: "targets", name: "robo", heading: "", label: "見此" });
    expect(parseLinkQuery("robo#閘|see")).toMatchObject({ mode: "noteHeadings", heading: "閘", label: "see" });
  });
  it("an empty or blank display text is no label", () => {
    expect(parseLinkQuery("robo|").label).toBe("");
    expect(parseLinkQuery("robo|   ").label).toBe("");
  });
  it("brackets typed beyond [[ are not part of the name", () => {
    expect(parseLinkQuery("[robo")).toMatchObject({ mode: "targets", name: "robo" });
  });
});

const hit = (note_id: number, kind: "paper" | "session" | "idea", title: string) => ({
  note_id,
  kind,
  target_id: note_id * 10,
  title,
});
const paper = (id: number, title: string) => ({
  paper_content_id: id,
  arxiv_id: null,
  title,
  abstract: null,
  year: 2025,
  is_private: false,
});

describe("linkItems", () => {
  const editor = () =>
    makeEditor([], [
      { id: "a", type: "heading", props: { level: 2 }, content: "動機" },
      { id: "b", type: "paragraph", content: "text" },
      { id: "c", type: "heading", props: { level: 3 }, content: "結論" },
    ]);

  it("lists notes (with their kind) then papers, and no create item", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, () =>
        HttpResponse.json([hit(12, "paper", "RoboTTT"), hit(13, "session", "Chat on robots"), hit(14, "idea", "Robot idea")]),
      ),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([paper(5, "Robots at scale")])),
    );
    const items = await linkItems(editor(), 7, "robo|見此");
    expect(items.map((i) => [i.group, i.icon, i.title])).toEqual([
      ["notes", "paperNote", "RoboTTT"],
      ["notes", "chatNote", "Chat on robots"],
      ["notes", "ideaNote", "Robot idea"],
      ["papers", "paper", "Robots at scale"],
    ]);
    expect(items[0]!.props).toEqual({
      targetKind: "note",
      targetId: 12,
      blockId: "",
      sectionKey: "",
      label: "見此",
      name: "RoboTTT",
    });
    expect(items[3]!.props).toMatchObject({ targetKind: "paper", targetId: 5, name: "Robots at scale", label: "見此" });
  });

  it("name# lists that note's outline, filtered, named title › heading", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, () => HttpResponse.json([hit(12, "paper", "RoboTTT")])),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/notes/12/outline`, () =>
        HttpResponse.json([
          { block_id: "s1", type: "section", text: "3 Method", level: 1 },
          { block_id: "h1", type: "heading", text: "閘控", level: 2 },
          { block_id: "h2", type: "heading", text: "結果", level: 2 },
        ]),
      ),
    );
    const items = await linkItems(editor(), 7, "RoboTTT#閘");
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ group: "headings", icon: "heading", title: "閘控" });
    expect(items[0]!.props).toEqual({
      targetKind: "note",
      targetId: 12,
      blockId: "h1",
      sectionKey: "",
      label: "",
      name: "RoboTTT › 閘控",
    });
    const all = await linkItems(editor(), 7, "RoboTTT#");
    expect(all.map((i) => [i.icon, i.title])).toEqual([
      ["section", "3 Method"],
      ["heading", "閘控"],
      ["heading", "結果"],
    ]);
  });

  it("a paper's # lists its sections", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([paper(5, "Attention")])),
      http.get(`${API_BASE_URL}/papers/content/5/sections`, () => HttpResponse.json(["1 Intro", "2 Method"])),
    );
    const items = await linkItems(editor(), 7, "attention#meth");
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ group: "sections", icon: "section", title: "2 Method" });
    expect(items[0]!.props).toEqual({
      targetKind: "paper",
      targetId: 5,
      blockId: "",
      sectionKey: "2 Method",
      label: "",
      name: "Attention › 2 Method",
    });
  });

  it("# lists this note's headings (as the editor has them)", async () => {
    useNotesStore.setState({
      notes: {
        7: {
          note: { id: 7, target_kind: "paper", target_id: "3", title: "Mine", revision: 1, updated_at: "" },
          blocks: [],
          suggestions: [],
          comments: [],
          revision: 1,
        },
      },
    });
    const items = await linkItems(editor(), 7, "#動");
    expect(items).toHaveLength(1);
    expect(items[0]!.props).toEqual({
      targetKind: "note",
      targetId: 7,
      blockId: "a",
      sectionKey: "",
      label: "",
      name: "Mine › 動機",
    });
  });

  it("## searches headings across all notes", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/headings/search`, ({ request }) => {
        expect(new URL(request.url).searchParams.get("q")).toBe("閘");
        return HttpResponse.json([{ note_id: 12, note_title: "RoboTTT", block_id: "h1", text: "閘控", level: 2 }]);
      }),
    );
    const items = await linkItems(editor(), 7, "##閘");
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ group: "headings", title: "閘控", subtext: "RoboTTT" });
    expect(items[0]!.props).toMatchObject({ targetId: 12, blockId: "h1", name: "RoboTTT › 閘控" });
  });

  it("an untitled note's link is named Untitled (with › heading)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, () => HttpResponse.json([hit(12, "paper", "")])),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([])),
    );
    const items = await linkItems(editor(), 7, "", "未命名");
    expect(items[0]).toMatchObject({ title: "未命名", props: { name: "未命名" } });
    useNotesStore.setState({
      notes: {
        7: {
          note: { id: 7, target_kind: "paper", target_id: "3", title: "", revision: 1, updated_at: "" },
          blocks: [],
          suggestions: [],
          comments: [],
          revision: 1,
        },
      },
    });
    const own = await linkItems(editor(), 7, "#動", "未命名");
    expect(own[0]!.props.name).toBe("未命名 › 動機");
  });

  it("name# with no exact title lists the targets whose title holds the whole text", async () => {
    const asked: string[] = [];
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, ({ request }) => {
        const q = new URL(request.url).searchParams.get("q") ?? "";
        asked.push(q);
        return HttpResponse.json(q === "C# notes" ? [hit(20, "session", "C# notes")] : [hit(21, "session", "C stuff")]);
      }),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([])),
    );
    const items = await linkItems(editor(), 7, "C# notes");
    expect(asked).toContain("C# notes");
    expect(items.map((i) => [i.group, i.title, i.props.targetId, i.props.blockId])).toEqual([["notes", "C# notes", 20, ""]]);
  });

  it("a search that fails leaves the other list", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/search`, () => HttpResponse.error()),
      http.get(`${API_BASE_URL}/papers/library`, () => HttpResponse.json([paper(5, "Robots")])),
    );
    const items = await linkItems(editor(), 7, "robo");
    expect(items.map((i) => i.title)).toEqual(["Robots"]);
  });
});
