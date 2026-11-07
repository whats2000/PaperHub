import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const { toast } = vi.hoisted(() => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }),
}));
vi.mock("sonner", () => ({ toast }));

import { API_BASE_URL } from "@/lib/api";
import { useIdeationStore } from "@/store/ideation";
import type { DocumentDomainsPut, DomainPatch, GraphResponse } from "@/types/ideation";

import { DocDomainEditor, DomainSettings } from "./DomainEditor";

const GRAPH: GraphResponse = {
  documents: [
    { id: 1, title: "Faster R-CNN", domains: [1, 2], idea_count: 1, is_private: false },
    { id: 2, title: "GPT-3", domains: [3], idea_count: 1, is_private: false },
  ],
  domains: [
    { id: 1, name: "Vision", color_index: 0 },
    { id: 2, name: "Detection", color_index: 1 },
    { id: 3, name: "Language", color_index: 2 },
  ],
  citations: [],
  library_count: 2,
  private_count: 0,
  ideas: [],
  problem: null,
};

const puts: DocumentDomainsPut[] = [];
const domainPatches: { id: number; body: DomainPatch }[] = [];
const server = setupServer(
  http.put(`${API_BASE_URL}/ideation/documents/:id/domains`, async ({ request, params }) => {
    const body = (await request.json()) as DocumentDomainsPut;
    puts.push(body);
    return HttpResponse.json({ id: Number(params.id), title: "Faster R-CNN", domains: body.domain_ids, is_private: false });
  }),
  http.patch(`${API_BASE_URL}/ideation/domains/:id`, async ({ request, params }) => {
    const body = (await request.json()) as DomainPatch;
    domainPatches.push({ id: Number(params.id), body });
    return HttpResponse.json(GRAPH.domains);
  }),
  http.get(`${API_BASE_URL}/ideation/graph`, () => HttpResponse.json(GRAPH)),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => server.resetHandlers());
beforeEach(() => {
  puts.length = 0;
  domainPatches.length = 0;
  toast.mockClear();
  toast.error.mockClear();
  useIdeationStore.setState({ scope: "explore", graph: GRAPH });
});

const doc = GRAPH.documents[0]!;

describe("DocDomainEditor", () => {
  it("removes a tag through the manual endpoint", async () => {
    const user = userEvent.setup();
    render(<DocDomainEditor doc={doc} domains={GRAPH.domains} onOpenDomain={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Remove Detection" }));
    await waitFor(() => expect(puts).toEqual([{ domain_ids: [1], new_names: [] }]));
  });

  it("removing two tags within the exit window sends one edit without both", async () => {
    const user = userEvent.setup();
    const three = { ...doc, domains: [1, 2, 3] };
    useIdeationStore.setState({ graph: { ...GRAPH, documents: [three, GRAPH.documents[1]!] } });
    render(<DocDomainEditor doc={three} domains={GRAPH.domains} onOpenDomain={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Remove Vision" }));
    await user.click(screen.getByRole("button", { name: "Remove Detection" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ domain_ids: [3], new_names: [] });
    // Still one edit after the refetch settles.
    await new Promise((r) => setTimeout(r, 250));
    expect(puts).toHaveLength(1);
  });

  it("closes the suggestions after choosing, until the user types again", async () => {
    const user = userEvent.setup();
    render(<DocDomainEditor doc={doc} domains={GRAPH.domains} onOpenDomain={vi.fn()} />);
    const input = screen.getByRole("combobox", { name: "Add a domain" });
    await user.type(input, "lang");
    await user.click(screen.getByRole("option", { name: /Language/ }));
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    expect(input).toHaveFocus();
    await waitFor(() => expect(input).not.toHaveAttribute("readonly"));
    await user.type(input, "x");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("keeps at least one domain", async () => {
    const user = userEvent.setup();
    const single = GRAPH.documents[1]!;
    render(<DocDomainEditor doc={single} domains={GRAPH.domains} onOpenDomain={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Remove Language" }));
    expect(toast).toHaveBeenCalledWith("A document needs at least one domain");
    expect(puts).toEqual([]);
  });

  it("adds an existing domain from the autocomplete, or creates a new one", async () => {
    const user = userEvent.setup();
    render(<DocDomainEditor doc={doc} domains={GRAPH.domains} onOpenDomain={vi.fn()} />);
    const input = screen.getByRole("combobox", { name: "Add a domain" });
    await user.type(input, "lang");
    const options = screen.getAllByRole("option");
    // Only domains the document lacks, then the create entry.
    expect(options.map((o) => o.textContent)).toEqual(["Language", "Create domain “lang”"]);
    await user.click(options[0]!);
    await waitFor(() => expect(puts).toEqual([{ domain_ids: [1, 2, 3], new_names: [] }]));

    await user.type(input, "Robotics{Enter}");
    await waitFor(() => expect(puts[1]).toEqual({ domain_ids: [1, 2], new_names: ["Robotics"] }));
  });

  it("clicking a tag opens the domain for rename or merge", async () => {
    const user = userEvent.setup();
    const onOpenDomain = vi.fn();
    render(<DocDomainEditor doc={doc} domains={GRAPH.domains} onOpenDomain={onOpenDomain} />);
    await user.click(screen.getByRole("button", { name: /^Detection$/ }));
    expect(onOpenDomain).toHaveBeenCalledWith(2);
  });
});

describe("DomainSettings", () => {
  it("explains a rename that collides with an existing domain", async () => {
    server.use(http.patch(`${API_BASE_URL}/ideation/domains/:id`, () => HttpResponse.json({ detail: "exists" }, { status: 409 })));
    const user = userEvent.setup();
    render(<DomainSettings domain={GRAPH.domains[1]!} domains={GRAPH.domains} onMerged={vi.fn()} />);
    const name = screen.getByRole("textbox", { name: "Rename" });
    await user.clear(name);
    await user.type(name, "Vision");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("A domain named “Vision” already exists — merge into it instead?"),
    );
  });

  it("re-picks the merge target when the chosen one disappears", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<DomainSettings domain={GRAPH.domains[1]!} domains={GRAPH.domains} onMerged={vi.fn()} />);
    // Vision (1) was the default target; it is merged away elsewhere.
    rerender(<DomainSettings domain={GRAPH.domains[1]!} domains={GRAPH.domains.filter((d) => d.id !== 1)} onMerged={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(domainPatches[0]).toEqual({ id: 2, body: { merge_into: 3 } }));
  });

  it("renames, and merges into another domain", async () => {
    const user = userEvent.setup();
    const onMerged = vi.fn();
    render(<DomainSettings domain={GRAPH.domains[1]!} domains={GRAPH.domains} onMerged={onMerged} />);
    const name = screen.getByRole("textbox", { name: "Rename" });
    await user.clear(name);
    await user.type(name, "Object detection");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(domainPatches[0]).toEqual({ id: 2, body: { name: "Object detection" } }));

    await user.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(domainPatches[1]).toEqual({ id: 2, body: { merge_into: 1 } }));
    await waitFor(() => expect(onMerged).toHaveBeenCalledWith(1));
  });
});

describe("patchDomain in a problem scope", () => {
  it("keeps only the scope's domains from the library-wide response", async () => {
    const scoped: GraphResponse = { ...GRAPH, domains: [GRAPH.domains[0]!], documents: [GRAPH.documents[0]!] };
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.patch(`${API_BASE_URL}/ideation/domains/:id`, () =>
        HttpResponse.json([{ ...GRAPH.domains[0]!, name: "Computer vision" }, ...GRAPH.domains.slice(1)]),
      ),
      http.get(`${API_BASE_URL}/ideation/graph`, async () => {
        await gate;
        return HttpResponse.json(scoped);
      }),
    );
    useIdeationStore.setState({ scope: 3, graph: scoped });
    const done = useIdeationStore.getState().patchDomain(1, { name: "Computer vision" });
    // The rename lands at once; the out-of-scope domains never enter the graph.
    await waitFor(() => expect(useIdeationStore.getState().graph?.domains[0]?.name).toBe("Computer vision"));
    expect(useIdeationStore.getState().graph?.domains.map((d) => d.id)).toEqual([1]);
    release();
    await done;
  });
});
