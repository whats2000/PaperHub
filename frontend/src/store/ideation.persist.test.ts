import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listIdeationProblems: vi.fn(),
  getIdeationGraph: vi.fn(),
  getIdeationExploreStatus: vi.fn(),
}));
vi.mock("@/lib/api", () => api);

const graph = { documents: [], domains: [], citations: [], ideas: [], problem: null, library_count: 0, private_count: 0 };
const problem = (id: number) => ({ id, text: "p", tried: "", created_at: "", idea_count: 0, status: "ready" });

async function freshStore() {
  vi.resetModules();
  return (await import("./ideation")).useIdeationStore;
}

describe("ideation store: remembered scope and view", () => {
  beforeEach(() => {
    localStorage.clear();
    api.listIdeationProblems.mockReset().mockResolvedValue([problem(7)]);
    api.getIdeationGraph.mockReset().mockResolvedValue(graph);
    api.getIdeationExploreStatus.mockReset().mockResolvedValue({ running: false });
  });

  it("restores the problem and view after a reload", async () => {
    let store = await freshStore();
    await store.getState().load();
    store.getState().setScope(7);
    store.getState().setView("cards");
    store = await freshStore();
    expect(store.getState().scope).toBe(7);
    expect(store.getState().view).toBe("cards");
    await store.getState().load();
    expect(store.getState().scope).toBe(7);
    expect(api.getIdeationGraph).toHaveBeenLastCalledWith(7);
  });

  it("falls back to free exploration when the remembered problem is gone", async () => {
    localStorage.setItem("paperhub-ideas-state", JSON.stringify({ scope: 99, view: "cards" }));
    const store = await freshStore();
    await store.getState().load();
    expect(store.getState().scope).toBe("explore");
    expect(store.getState().view).toBe("cards");
    expect(api.getIdeationGraph).toHaveBeenLastCalledWith("explore");
    expect((JSON.parse(localStorage.getItem("paperhub-ideas-state") ?? "{}") as { scope: unknown }).scope).toBe("explore");
  });

  it("keeps a remembered problem when the problem list failed to load", async () => {
    localStorage.setItem("paperhub-ideas-state", JSON.stringify({ scope: 99, view: "graph" }));
    api.listIdeationProblems.mockRejectedValue(new Error("down"));
    const store = await freshStore();
    await store.getState().load();
    expect(store.getState().scope).toBe(99);
  });

  it("starts from the defaults when storage is unreadable", async () => {
    localStorage.setItem("paperhub-ideas-state", "{not json");
    const store = await freshStore();
    expect(store.getState().scope).toBe("explore");
    expect(store.getState().view).toBe("graph");
  });
});
