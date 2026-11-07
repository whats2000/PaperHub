import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DESELECT_DELAY_MS, IdeasPage } from "@/pages/IdeasPage";
import { useIdeationStore } from "@/store/ideation";
import type { GraphResponse, Idea, IdeaStatus } from "@/types/ideation";

import { IdeaDrawer } from "./IdeaDrawer";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const idea = (id: number, status: IdeaStatus, source: number, target: number | null): Idea => ({
  id,
  problem_id: target === null ? 7 : null,
  source_doc: source,
  source_section: "Region Proposal Networks",
  source_chunk_ids: [10],
  target_doc: target,
  target_section: target === null ? null : "Few-shot prompting",
  target_chunk_ids: target === null ? [] : [20],
  title: `Idea ${id}`,
  rationale: `Rationale for ${id}.`,
  status,
  prev_status: null,
  note: "",
  dismiss_reasons: [],
  session_ids: [],
  created_at: "2026-11-05T10:00:00Z",
});

function graph(overrides: Partial<GraphResponse> = {}): GraphResponse {
  return {
    documents: [
      { id: 1, title: "Faster R-CNN", domains: [1], idea_count: 2, is_private: false },
      { id: 2, title: "GPT-3", domains: [2], idea_count: 1, is_private: true },
      { id: 3, title: "Hybrid", domains: [1, 2], idea_count: 0, is_private: false },
    ],
    domains: [
      { id: 1, name: "Vision", color_index: 0 },
      { id: 2, name: "Language", color_index: 1 },
      { id: 3, name: "Misc", color_index: null },
    ],
    citations: [{ source_id: 1, target_id: 2 }],
    library_count: 3,
    private_count: 1,
    ideas: [idea(11, "saved", 1, 2), idea(12, "new", 2, 1)],
    problem: null,
    ...overrides,
  };
}

const problemGraph = () =>
  graph({
    ideas: [idea(21, "new", 1, null), idea(22, "trying", 2, null)],
    problem: {
      id: 7,
      text: "Rerank retrieved images for a product search engine",
      tried: "CLIP similarity reranking",
      created_at: "2026-11-05T10:00:00Z",
      idea_count: 2,
      status: "ready",
    },
  });

describe("IdeaDrawer", () => {
  it("pans again when the graph comes back into view with a selection", () => {
    const pan = vi.fn();
    const anchors = () => [[100, 200]] as [number, number][];
    const g = graph();
    const props = { graph: g, ideas: g.ideas, selected: { kind: "idea", id: 11 } as const, onSelect: vi.fn(), anchorsFor: anchors };
    const { rerender } = render(<IdeaDrawer {...props} />);
    expect(pan).not.toHaveBeenCalled();
    rerender(<IdeaDrawer {...props} panToKeepVisible={pan} />);
    expect(pan).toHaveBeenCalledTimes(1);
    rerender(<IdeaDrawer {...props} panToKeepVisible={pan} />);
    expect(pan).toHaveBeenCalledTimes(1);
  });

  it("ignores Esc during IME composition and from a field", () => {
    const onSelect = vi.fn();
    render(
      <>
        <textarea aria-label="note" />
        <IdeaDrawer graph={graph()} ideas={graph().ideas} selected={{ kind: "idea", id: 11 }} onSelect={onSelect} />
      </>,
    );
    fireEvent.keyDown(document.body, { key: "Escape", isComposing: true });
    fireEvent.keyDown(screen.getByLabelText("note"), { key: "Escape" });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("renders nothing when nothing is selected", () => {
    render(<IdeaDrawer graph={graph()} ideas={graph().ideas} selected={null} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("idea-drawer")).toBeNull();
  });

  it("is a fixed 340 px panel sliding with transform", () => {
    render(<IdeaDrawer graph={graph()} ideas={graph().ideas} selected={{ kind: "idea", id: 11 }} onSelect={vi.fn()} />);
    const drawer = screen.getByTestId("idea-drawer");
    expect(drawer).toHaveClass("absolute", "right-0", "w-[340px]", "transition-transform");
  });

  it("calls panToKeepVisible with the idea's anchors and inset 340 on idea selection", () => {
    const pan = vi.fn();
    const anchors = vi.fn(() => [[100, 200], [700, 220]] as [number, number][]);
    const g = graph();
    const { rerender } = render(
      <IdeaDrawer graph={g} ideas={g.ideas} selected={null} onSelect={vi.fn()} panToKeepVisible={pan} anchorsFor={anchors} />,
    );
    expect(pan).not.toHaveBeenCalled();
    rerender(
      <IdeaDrawer
        graph={g}
        ideas={g.ideas}
        selected={{ kind: "idea", id: 11 }}
        onSelect={vi.fn()}
        panToKeepVisible={pan}
        anchorsFor={anchors}
      />,
    );
    expect(anchors).toHaveBeenCalledWith({ kind: "idea", id: 11 });
    expect(pan).toHaveBeenCalledWith([[100, 200], [700, 220]], 340);
  });

  it("shows an idea's source, target and rationale", () => {
    render(<IdeaDrawer graph={graph()} ideas={graph().ideas} selected={{ kind: "idea", id: 11 }} onSelect={vi.fn()} />);
    const d = within(screen.getByTestId("idea-drawer"));
    expect(d.getByRole("heading", { name: "Idea 11" })).toBeInTheDocument();
    expect(d.getByText("Faster R-CNN")).toBeInTheDocument();
    expect(d.getByText("Region Proposal Networks")).toBeInTheDocument();
    expect(d.getByText("GPT-3")).toBeInTheDocument();
    expect(d.getByText("Rationale for 11.")).toBeInTheDocument();
  });

  it("jumps to the source and target documents from their cards", async () => {
    const onSelect = vi.fn();
    render(<IdeaDrawer graph={graph()} ideas={graph().ideas} selected={{ kind: "idea", id: 11 }} onSelect={onSelect} />);
    const d = within(screen.getByTestId("idea-drawer"));
    await userEvent.click(d.getByRole("button", { name: /Faster R-CNN/ }));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: "doc", id: 1 });
    await userEvent.click(d.getByRole("button", { name: /GPT-3/ }));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: "doc", id: 2 });
  });

  it("jumps to the problem from a problem idea's target card", async () => {
    const onSelect = vi.fn();
    const g = problemGraph();
    const id = g.ideas[0]!.id;
    render(<IdeaDrawer graph={g} ideas={g.ideas} selected={{ kind: "idea", id }} onSelect={onSelect} />);
    await userEvent.click(within(screen.getByTestId("idea-drawer")).getByRole("button", { name: /The problem you described/ }));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: "problem" });
  });

  it("shows the problem's full text, what was tried, and its idea list", () => {
    const onSelect = vi.fn();
    const g = problemGraph();
    render(<IdeaDrawer graph={g} ideas={g.ideas} selected={{ kind: "problem" }} onSelect={onSelect} />);
    const d = within(screen.getByTestId("idea-drawer"));
    expect(d.getByText("Rerank retrieved images for a product search engine")).toBeInTheDocument();
    expect(d.getByText("CLIP similarity reranking")).toBeInTheDocument();
    fireEvent.click(d.getByRole("button", { name: /Idea 22/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: "idea", id: 22 });
  });

  it("shows a document's domains and related ideas", () => {
    render(<IdeaDrawer graph={graph()} ideas={graph().ideas} selected={{ kind: "doc", id: 3 }} onSelect={vi.fn()} />);
    const d = within(screen.getByTestId("idea-drawer"));
    expect(d.getByRole("heading", { name: "Hybrid" })).toBeInTheDocument();
    expect(d.getByText("Vision")).toBeInTheDocument();
    expect(d.getByText("Language")).toBeInTheDocument();
  });

  it("shows a domain's documents", () => {
    const onSelect = vi.fn();
    render(<IdeaDrawer graph={graph()} ideas={graph().ideas} selected={{ kind: "domain", id: 1 }} onSelect={onSelect} />);
    const d = within(screen.getByTestId("idea-drawer"));
    expect(d.getByRole("heading", { name: "Vision" })).toBeInTheDocument();
    fireEvent.click(d.getByRole("button", { name: /Hybrid/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: "doc", id: 3 });
  });

  it("renders a caller's content slot for the selection kind", () => {
    render(
      <IdeaDrawer
        graph={graph()}
        ideas={graph().ideas}
        selected={{ kind: "idea", id: 11 }}
        onSelect={vi.fn()}
        slots={{ idea: (i) => <p>slot for {i.id}</p> }}
      />,
    );
    expect(screen.getByText("slot for 11")).toBeInTheDocument();
  });

  it("closes on the ✕ button and on Esc", () => {
    const onSelect = vi.fn();
    render(<IdeaDrawer graph={graph()} ideas={graph().ideas} selected={{ kind: "idea", id: 11 }} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onSelect).toHaveBeenLastCalledWith(null);
    onSelect.mockClear();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });
});

vi.mock("@/lib/api", async (importOriginal) => {
  const orig = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...orig,
    listIdeationProblems: vi.fn(() => Promise.resolve([])),
    getIdeationGraph: vi.fn(() => Promise.resolve(graph())),
  };
});

describe("IdeasPage graph interaction", () => {
  beforeEach(() => {
    useIdeationStore.setState({
      problems: [],
      scope: "explore",
      view: "graph",
      filter: "active",
      selected: null,
      graph: null,
      loading: false,
      error: null,
      listError: null,
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    useIdeationStore.getState().stopPolling();
  });

  /** The zoom level the control shows, as a factor. */
  const zoomLevel = () => Number(/(\d+)%/.exec(screen.getByTestId("zoom-control").textContent ?? "")?.[1]) / 100;

  async function openOnNode() {
    render(<IdeasPage />);
    const svg = await screen.findByTestId("idea-graph");
    // Nothing is selected on entry.
    expect(screen.queryByTestId("idea-drawer")).toBeNull();
    fireEvent.click(screen.getByTestId("node-1"));
    expect(screen.getByTestId("idea-drawer")).toHaveAttribute("data-state", "open");
    return svg;
  }

  it("closing from the cards view gives focus back to the idea's card", async () => {
    const user = userEvent.setup();
    useIdeationStore.setState({ view: "cards" });
    render(<IdeasPage />);
    const card = await screen.findByTestId("idea-card-11");
    await user.click(card);
    const drawer = screen.getByTestId("idea-drawer");
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(document.activeElement).toBe(card);
  });

  it("a drag that ends over empty space keeps the drawer open", async () => {
    const svg = await openOnNode();
    act(() => {
      fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
      fireEvent.pointerMove(svg, { pointerId: 1, clientX: 110, clientY: 100 });
      fireEvent.pointerUp(svg, { pointerId: 1, clientX: 110, clientY: 100 });
      fireEvent.click(svg, { clientX: 110, clientY: 100 });
    });
    expect(useIdeationStore.getState().selected).toEqual({ kind: "doc", id: 1 });
    expect(screen.getByTestId("idea-drawer")).toHaveAttribute("data-state", "open");
  });

  const plainClick = (el: Element, detail = 1) => {
    fireEvent.pointerDown(el, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerUp(el, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.click(el, { clientX: 100, clientY: 100, detail });
  };

  it("a click on empty space closes the drawer with a 200 ms exit", async () => {
    const svg = await openOnNode();
    vi.useFakeTimers();
    act(() => plainClick(svg));
    // The deselect waits for a possible second click first.
    act(() => {
      vi.advanceTimersByTime(DESELECT_DELAY_MS);
    });
    expect(useIdeationStore.getState().selected).toBeNull();
    // Still in the DOM right after (sliding out)...
    expect(screen.getByTestId("idea-drawer")).toHaveAttribute("data-state", "closed");
    act(() => {
      vi.advanceTimersByTime(199);
    });
    expect(screen.getByTestId("idea-drawer")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    // ...and gone once the exit has played.
    expect(screen.queryByTestId("idea-drawer")).toBeNull();
  });

  it("a double-click on empty space zooms without closing the drawer", async () => {
    const svg = await openOnNode();
    const before = zoomLevel();
    vi.useFakeTimers();
    act(() => {
      plainClick(svg, 1);
      plainClick(svg, 2);
      fireEvent.doubleClick(svg, { clientX: 100, clientY: 100 });
    });
    act(() => {
      vi.advanceTimersByTime(DESELECT_DELAY_MS + 50);
    });
    expect(useIdeationStore.getState().selected).toEqual({ kind: "doc", id: 1 });
    expect(zoomLevel()).toBeCloseTo(Math.min(3, before * 1.5), 1);
  });

  const dragFrom = (node: Element, svg: Element) => {
    fireEvent.pointerDown(node, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(node, { pointerId: 1, clientX: 110, clientY: 100 });
    fireEvent.pointerUp(node, { pointerId: 1, clientX: 110, clientY: 100 });
    // Whichever element the browser sends the ending click to, it is ignored.
    fireEvent.click(node);
    fireEvent.click(svg);
  };

  it("a drag that starts on a node selects nothing (drawer closed)", async () => {
    render(<IdeasPage />);
    const svg = await screen.findByTestId("idea-graph");
    act(() => dragFrom(screen.getByTestId("node-1"), svg));
    expect(useIdeationStore.getState().selected).toBeNull();
    expect(screen.queryByTestId("idea-drawer")).toBeNull();
  });

  it("a drag that starts on a node keeps another node's drawer as it was", async () => {
    render(<IdeasPage />);
    const svg = await screen.findByTestId("idea-graph");
    fireEvent.click(screen.getByTestId("node-2"));
    vi.useFakeTimers();
    act(() => dragFrom(screen.getByTestId("node-1"), svg));
    act(() => {
      vi.advanceTimersByTime(DESELECT_DELAY_MS + 50);
    });
    expect(useIdeationStore.getState().selected).toEqual({ kind: "doc", id: 2 });
    expect(screen.getByTestId("idea-drawer")).toHaveAttribute("data-state", "open");
  });

  it("Esc on the page closes the drawer", async () => {
    await openOnNode();
    act(() => {
      fireEvent.keyDown(document.body, { key: "Escape" });
    });
    expect(useIdeationStore.getState().selected).toBeNull();
    expect(screen.getByTestId("idea-drawer")).toHaveAttribute("data-state", "closed");
  });

  it("Esc in a text field leaves the drawer open", async () => {
    await openOnNode();
    const input = screen.getByPlaceholderText(/Describe a new problem/);
    input.focus();
    act(() => {
      fireEvent.keyDown(input, { key: "Escape" });
    });
    expect(useIdeationStore.getState().selected).toEqual({ kind: "doc", id: 1 });
  });

  it("Esc with the problem switcher open closes only the switcher", async () => {
    await openOnNode();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^Problem:/ }));
    expect(await screen.findByRole("listbox", { name: "Problems" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("listbox", { name: "Problems" })).toBeNull());
    expect(useIdeationStore.getState().selected).toEqual({ kind: "doc", id: 1 });
    expect(screen.getByTestId("idea-drawer")).toHaveAttribute("data-state", "open");
  });

  it("closing from inside the drawer returns focus to the selected node", async () => {
    await openOnNode();
    const close = screen.getByRole("button", { name: "Close" });
    close.focus();
    fireEvent.click(close);
    expect(useIdeationStore.getState().selected).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("node-1"));
  });

  /** The graph viewport's transform: translate(x, y) scale(k). */
  const viewTransform = () => {
    const m = /translate\(([-\d.e]+)px, ([-\d.e]+)px\) scale\(([-\d.e]+)\)/.exec(
      screen.getByTestId("graph-viewport").style.transform,
    );
    return m ? { x: Number(m[1]), y: Number(m[2]), k: Number(m[3]) } : null;
  };

  it("frames the drawing on entry", async () => {
    render(<IdeasPage />);
    const svg = await screen.findByTestId("idea-graph");
    const [, , vw, vh] = svg.getAttribute("viewBox")!.split(" ").map(Number);
    const t = viewTransform()!;
    expect(t.k).not.toBe(1);
    for (const id of ["node-1", "node-2"]) {
      const body = within(screen.getByTestId(id)).getByTestId("node-body");
      const x = Number(body.getAttribute("cx")) * t.k + t.x;
      const y = Number(body.getAttribute("cy")) * t.k + t.y;
      expect(x).toBeGreaterThan(0);
      expect(x).toBeLessThan(vw!);
      expect(y).toBeGreaterThan(0);
      expect(y).toBeLessThan(vh!);
    }
  });

  it("re-frames when documents arrive, unless the reader moved the view", async () => {
    render(<IdeasPage />);
    await screen.findByTestId("idea-graph");
    const first = viewTransform();
    const more = (n: number) =>
      graph({
        documents: [
          ...graph().documents,
          ...Array.from({ length: n }, (_, i) => ({
            id: 100 + i,
            title: `Extra ${i}`,
            domains: [2],
            idea_count: 0,
            is_private: false,
          })),
        ],
      });
    act(() => useIdeationStore.setState({ graph: more(6) }));
    const second = viewTransform();
    expect(second).not.toEqual(first);
    // A count-only refresh never moves the view.
    act(() => useIdeationStore.setState({ graph: { ...more(6), documents: more(6).documents.map((d) => ({ ...d, idea_count: 3 })) } }));
    expect(viewTransform()).toEqual(second);
    // Once the reader zooms, new documents leave their view alone.
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    const zoomed = viewTransform();
    act(() => useIdeationStore.setState({ graph: more(12) }));
    expect(viewTransform()).toEqual(zoomed);
  });

  it("the zoom control moves aside when the drawer opens; the legend never moves", async () => {
    render(<IdeasPage />);
    await screen.findByTestId("idea-graph");
    const zoom = screen.getByTestId("zoom-control");
    const legend = screen.getByTestId("graph-legend");
    const legendClass = legend.className;
    expect(zoom).not.toHaveClass("-translate-x-[340px]");
    const before = zoomLevel();
    fireEvent.click(screen.getByTestId("node-1"));
    expect(zoom).toHaveClass("-translate-x-[340px]");
    expect(legend.className).toBe(legendClass);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(zoomLevel()).toBeCloseTo(Math.min(3, before * 1.25), 1);
  });

  it("the legend is grouped and collapsible", async () => {
    render(<IdeasPage />);
    await screen.findByTestId("idea-graph");
    const legend = within(screen.getByTestId("graph-legend"));
    expect(screen.getByTestId("graph-legend")).toHaveClass("opacity-55", "hover:opacity-100");
    expect(legend.getByText("Vision")).toBeInTheDocument();
    expect(legend.getByText("Other")).toBeInTheDocument();
    const toggle = legend.getByRole("button", { name: /Legend/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});
