import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { GraphResponse, Idea, IdeaStatus } from "@/types/ideation";

import { textWidth } from "@/lib/ideaGraphLayout";
import { svgSafeId } from "@/lib/svgId";

import { IdeaGraph } from "./IdeaGraph";

const idea = (id: number, status: IdeaStatus, source: number, target: number | null): Idea => ({
  id,
  problem_id: target === null ? 1 : null,
  source_doc: source,
  source_section: "Method",
  source_chunk_ids: [10],
  target_doc: target,
  target_section: target === null ? null : "Model",
  target_chunk_ids: target === null ? [] : [20],
  title: `Idea ${id}`,
  rationale: "Because.",
  status,
  prev_status: null,
  note: "",
  dismiss_reasons: [],
  session_ids: [],
  created_at: "2026-11-05T10:00:00Z",
});

/** var()-based paint lives in `style`, not presentation attributes. */
function paintOf(el: Element): CSSStyleDeclaration {
  return (el as SVGElement).style;
}

function graph(overrides: Partial<GraphResponse> = {}): GraphResponse {
  return {
    documents: [
      { id: 1, title: "Faster R-CNN", domains: [1], idea_count: 2, is_private: false },
      { id: 2, title: "GPT-3", domains: [2], idea_count: 1, is_private: false },
      { id: 3, title: "Hybrid", domains: [1, 2], idea_count: 0, is_private: true },
      { id: 4, title: "Loose", domains: [3], idea_count: 0, is_private: false },
    ],
    domains: [
      { id: 1, name: "Vision", color_index: 0 },
      { id: 2, name: "Language", color_index: 1 },
      { id: 3, name: "Misc", color_index: null },
    ],
    citations: [{ source_id: 1, target_id: 2 }],
    library_count: 4,
    private_count: 1,
    ideas: [idea(11, "dismissed", 1, 2), idea(12, "saved", 2, 1), idea(13, "new", 1, 2)],
    problem: null,
    ...overrides,
  };
}

describe("IdeaGraph", () => {
  it("draws dismissed and failed ideas dotted, kept ones solid, new ones dashed", () => {
    render(<IdeaGraph graph={graph({ ideas: [idea(11, "dismissed", 1, 2), idea(12, "saved", 2, 1), idea(13, "new", 1, 2), idea(14, "failed", 2, 1)] })} />);
    expect(screen.getByTestId("idea-line-11")).toHaveAttribute("stroke-dasharray", "1.5 4");
    expect(screen.getByTestId("idea-line-14")).toHaveAttribute("stroke-dasharray", "1.5 4");
    expect(screen.getByTestId("idea-line-12")).not.toHaveAttribute("stroke-dasharray");
    expect(screen.getByTestId("idea-line-13")).toHaveAttribute("stroke-dasharray", "5 4");
    // Every idea line is the same ink; status is never colour.
    const strokes = ["11", "12", "13", "14"].map((id) => paintOf(screen.getByTestId(`idea-line-${id}`)).stroke);
    expect(new Set(strokes)).toEqual(new Set(["var(--foreground)"]));
  });

  it("split-fills a multi-domain node with two half-circle paths in its domains' colours", () => {
    render(<IdeaGraph graph={graph()} />);
    const halves = within(screen.getByTestId("node-3")).getAllByTestId("node-half");
    expect(halves).toHaveLength(2);
    expect(halves.map((h) => paintOf(h).fill)).toEqual(["var(--domain-1)", "var(--domain-2)"]);
    expect(within(screen.getByTestId("node-1")).queryAllByTestId("node-half")).toHaveLength(0);
  });

  it("paints domains from the server's color_index, null as gray Other", () => {
    render(<IdeaGraph graph={graph()} />);
    const fill = (id: string) => paintOf(within(screen.getByTestId(id)).getByTestId("node-body")).fill;
    expect(fill("node-2")).toBe("var(--domain-2)");
    expect(fill("node-4")).toBe("var(--muted-foreground)");
    const hull = paintOf(screen.getByTestId("hull-1").querySelector("polygon")!);
    expect(hull.fill).toBe("var(--domain-1)");
    expect(hull.fillOpacity).toBe("var(--hull-alpha)");
  });

  it("falls back to the client rule for a domain without a color_index", () => {
    const g = graph();
    const domains = g.domains.map((d) => (d.id === 3 ? { id: 3, name: "Misc" } : d)) as GraphResponse["domains"];
    render(<IdeaGraph graph={{ ...g, domains }} />);
    const fill = paintOf(within(screen.getByTestId("node-4")).getByTestId("node-body")).fill;
    expect(fill).toMatch(/^var\(--domain-[3-8]\)$/);
  });

  it("marks private documents with a lock, draws citations and status badges", () => {
    render(<IdeaGraph graph={graph()} />);
    expect(within(screen.getByTestId("node-3")).getByTestId("node-lock")).toBeInTheDocument();
    expect(within(screen.getByTestId("node-1")).queryByTestId("node-lock")).toBeNull();
    expect(paintOf(screen.getByTestId("citation")).stroke).toBe("var(--muted-foreground)");
    expect(screen.getByTestId("idea-badge-12")).toHaveAttribute("data-status", "saved");
    expect(screen.queryByTestId("idea-badge-13")).toBeNull(); // new ideas carry no badge
  });

  it("highlights a selected idea: solid and bold, endpoint rings, the rest dimmed", () => {
    render(<IdeaGraph graph={graph()} selected={{ kind: "idea", id: 11 }} />);
    const line = screen.getByTestId("idea-line-11");
    expect(line).not.toHaveAttribute("stroke-dasharray");
    expect(line).toHaveAttribute("stroke-width", "2.6");
    expect(within(screen.getByTestId("node-1")).getByText("Source")).toBeInTheDocument();
    expect(within(screen.getByTestId("node-2")).getByText("Target")).toBeInTheDocument();
    expect(screen.getByTestId("idea-12")).toHaveAttribute("opacity", "0.3");
    expect(screen.getByTestId("node-4")).toHaveAttribute("opacity", "0.3");
    expect(screen.getByTestId("node-1")).toHaveAttribute("opacity", "1");
  });

  it("dims nothing when nothing is selected", () => {
    render(<IdeaGraph graph={graph()} />);
    for (const el of screen.getAllByTestId(/^(node|idea|hull)-\d+$/)) expect(el).toHaveAttribute("opacity", "1");
  });

  it("draws a problem-mode idea into the dark problem node", () => {
    const g = graph({
      ideas: [idea(21, "trying", 1, null)],
      problem: { id: 1, text: "Rerank retrieved images", tried: "", created_at: "", idea_count: 1, status: "ready" },
    });
    render(<IdeaGraph graph={g} selected={{ kind: "idea", id: 21 }} />);
    const problem = screen.getByTestId("problem-node");
    expect(paintOf(within(problem).getByTestId("problem-body")).fill).toBe("var(--foreground)");
    expect(within(problem).getByText("Target")).toBeInTheDocument();
    expect(screen.getByTestId("idea-badge-21")).toHaveAttribute("data-status", "trying");
  });

  it("reports node and idea clicks as selections", async () => {
    const onSelect = vi.fn();
    render(<IdeaGraph graph={graph()} onSelect={onSelect} />);
    await userEvent.click(screen.getByTestId("node-2"));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: "doc", id: 2 });
    await userEvent.click(screen.getByRole("button", { name: /Idea 12/ }));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: "idea", id: 12 });
  });

  it("gives each arrowhead its line's status opacity", () => {
    render(<IdeaGraph graph={graph({ ideas: [idea(11, "dismissed", 1, 2), idea(12, "saved", 2, 1)] })} />);
    const head = (id: string) => {
      const url = screen.getByTestId(`idea-line-${id}`).getAttribute("marker-end")!;
      const markerId = /url\(#(.+)\)/.exec(url)![1];
      return document.getElementById(markerId ?? "")!.querySelector("path")!.getAttribute("fill-opacity");
    };
    expect(head("11")).toBe("0.4");
    expect(head("12")).toBe("0.8");
  });

  it("shows a focus indicator on the keyboard-focused node, idea and problem", async () => {
    const g = graph({
      ideas: [idea(21, "new", 1, null)],
      problem: { id: 1, text: "Rerank retrieved images", tried: "", created_at: "", idea_count: 1, status: "ready" },
    });
    render(<IdeaGraph graph={g} selected={{ kind: "doc", id: 2 }} />);
    const node = screen.getByTestId("node-1");
    await userEvent.tab();
    // Tab order: the idea comes first in the DOM, then the problem, then the nodes.
    const ideaGroup = screen.getByTestId("idea-21");
    expect(document.activeElement).toBe(ideaGroup);
    expect(ideaGroup).toHaveClass("group", "focus-visible:opacity-100");
    expect(screen.getByTestId("idea-line-21")).toHaveClass("group-focus-visible:[stroke-width:2.6]");
    await userEvent.tab();
    const problem = screen.getByTestId("problem-node");
    expect(document.activeElement).toBe(problem);
    expect(within(problem).getByTestId("focus-ring")).toHaveClass("opacity-0", "group-focus-visible:opacity-100");
    await userEvent.tab();
    expect(document.activeElement).toBe(node);
    // Dimmed by the selection, but focus lifts it and shows the ring.
    expect(node).toHaveAttribute("opacity", "0.3");
    expect(node).toHaveClass("group", "focus-visible:opacity-100");
    expect(within(node).getByTestId("focus-ring")).toHaveClass("group-focus-visible:opacity-100");
  });

  it("makes the hull label a keyboard button when a domain can be picked", async () => {
    const onSelectDomain = vi.fn();
    const { rerender } = render(<IdeaGraph graph={graph()} />);
    expect(screen.getByTestId("hull-label-1")).not.toHaveAttribute("role");
    rerender(<IdeaGraph graph={graph()} onSelectDomain={onSelectDomain} />);
    const label = screen.getByRole("button", { name: "Vision" });
    label.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelectDomain).toHaveBeenCalledWith(1);
    await userEvent.keyboard(" ");
    expect(onSelectDomain).toHaveBeenCalledTimes(2);
  });

  it("keeps nodes near their place when an idea count changes, and glides there", async () => {
    const before = graph();
    const { rerender } = render(<IdeaGraph graph={before} />);
    const pos = (id: number) => {
      const c = within(screen.getByTestId(`node-${id}`)).getByTestId("node-body");
      return { x: Number(c.getAttribute("cx")), y: Number(c.getAttribute("cy")), r: Number(c.getAttribute("r")) };
    };
    const start = pos(2);
    const after = {
      ...before,
      documents: before.documents.map((d) => (d.id === 2 ? { ...d, idea_count: 5 } : d)),
    };
    rerender(<IdeaGraph graph={after} />);
    // The tween starts from the frame on screen: nothing jumps on the rerender.
    expect(pos(2)).toEqual(start);
    await act(() => new Promise((r) => setTimeout(r, 400)));
    const end = pos(2);
    expect(end.r).toBeCloseTo(8 + 3 * Math.sqrt(5), 1);
    expect(Math.hypot(end.x - start.x, end.y - start.y)).toBeLessThan(40);
  });

  it("jumps straight to the new layout under reduced motion", () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({ ...original(q), matches: q.includes("reduce") }));
    try {
      const before = graph();
      const { rerender } = render(<IdeaGraph graph={before} />);
      const after = { ...before, documents: before.documents.map((d) => (d.id === 2 ? { ...d, idea_count: 5 } : d)) };
      rerender(<IdeaGraph graph={after} />);
      const r = Number(within(screen.getByTestId("node-2")).getByTestId("node-body").getAttribute("r"));
      expect(r).toBeCloseTo(8 + 3 * Math.sqrt(5), 1);
    } finally {
      window.matchMedia = original;
    }
  });
  it("sanitizes ids used inside url(#…)", () => {
    expect(svgSafeId(":r1:")).toBe("r1");
    expect(svgSafeId("«r1»")).toBe("r1");
    expect(svgSafeId("_r_1_")).toBe("_r_1_");
    render(<IdeaGraph graph={graph()} />);
    for (const id of ["11", "12", "13"]) {
      expect(screen.getByTestId(`idea-line-${id}`).getAttribute("marker-end")).toMatch(/^url\(#[A-Za-z0-9_-]+\)$/);
    }
  });

  it("gives the keyboard-focused idea a full-opacity arrowhead, like its line", async () => {
    render(<IdeaGraph graph={graph({ ideas: [idea(11, "dismissed", 1, 2)] })} />);
    const head = () => {
      const url = screen.getByTestId("idea-line-11").getAttribute("marker-end")!;
      const id = /url\(#(.+)\)/.exec(url)![1];
      return document.getElementById(id ?? "")!.querySelector("path")!.getAttribute("fill-opacity");
    };
    expect(head()).toBe("0.4");
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByTestId("idea-11"));
    expect(head()).toBe("1");
    await userEvent.tab();
    expect(head()).toBe("0.4");
  });

  it("dims everything outside a selected domain", () => {
    render(<IdeaGraph graph={graph()} selected={{ kind: "domain", id: 3 }} />);
    expect(screen.getByTestId("node-4")).toHaveAttribute("opacity", "1");
    expect(screen.getByTestId("node-1")).toHaveAttribute("opacity", "0.3");
  });

  it("applies the pan/zoom transform to the drawing and reports its layout", () => {
    const onLayout = vi.fn();
    const { rerender } = render(<IdeaGraph graph={graph()} viewTransform={{ x: 10, y: 20, k: 2 }} onLayout={onLayout} />);
    const vp = screen.getByTestId("graph-viewport");
    expect(vp.style.transform).toBe("translate(10px, 20px) scale(2)");
    expect(vp).toHaveClass("transition-transform");
    expect((onLayout.mock.calls[0]?.[0] as { nodes: unknown[] }).nodes).toHaveLength(4);
    rerender(<IdeaGraph graph={graph()} viewTransform={{ x: 10, y: 20, k: 2 }} viewLive onLayout={onLayout} />);
    expect(screen.getByTestId("graph-viewport")).not.toHaveClass("transition-transform");
  });

  it("cuts a long CJK problem to the pill's width with an ellipsis at the end only", () => {
    const text = "我們的機器人操作策略在推論時需要許多昂貴的去噪步驟所以控制迴圈太慢";
    const problem = { id: 1, text, tried: "", created_at: "", idea_count: 0, status: "ready" as const };
    render(<IdeaGraph graph={graph({ problem })} />);
    const shown = within(screen.getByTestId("problem-node")).getAllByText(/…$/)[0]?.textContent ?? "";
    expect(shown.indexOf("…")).toBe(shown.length - 1);
    expect(text.startsWith(shown.slice(0, -1))).toBe(true);
    expect(textWidth(shown, 9.5)).toBeLessThanOrEqual(112);
  });

  it("hides a label that collides, and reveals it on hover through the group", () => {
    const documents = [1, 2, 3, 4, 5].map((id) => ({
      id,
      title: `A rather long document title number ${id}`,
      domains: [1],
      idea_count: 0,
      is_private: false,
    }));
    render(<IdeaGraph graph={graph({ documents, ideas: [], citations: [] })} />);
    const modes = documents.map((d) => screen.getByTestId(`node-label-${d.id}`).getAttribute("data-label"));
    expect(modes).toContain("full");
    for (const d of documents) {
      const label = screen.getByTestId(`node-label-${d.id}`);
      if (label.getAttribute("data-label") === "hidden") {
        expect(label).toHaveClass("opacity-0", "group-hover:opacity-100", "transition-opacity");
      }
    }
  });
});

describe("IdeaGraph legend highlight", () => {
  const op = (id: string) => screen.getByTestId(id).getAttribute("opacity");

  it("lights one domain's documents and hull and dims the rest", () => {
    render(<IdeaGraph graph={graph()} highlight={{ kind: "domains", ids: [3] }} />);
    expect(op("node-4")).toBe("1");
    expect(op("node-1")).toBe("0.3");
    expect(op("hull-3")).toBe("1");
    // A document in two domains does not keep the other domain's hull lit.
    expect(op("hull-1")).toBe("0.3");
  });

  it("lights the citations only", () => {
    render(<IdeaGraph graph={graph()} highlight={{ kind: "citations" }} />);
    expect(screen.getByTestId("citation")).toHaveAttribute("opacity", "1");
    expect(op("idea-11")).toBe("0.3");
    expect(op("node-1")).toBe("1");
    expect(op("node-4")).toBe("0.3");
  });

  it("lights the ideas of the hovered statuses and their documents", () => {
    render(<IdeaGraph graph={graph()} highlight={{ kind: "status", statuses: ["saved"] }} />);
    expect(op("idea-12")).toBe("1");
    expect(op("idea-11")).toBe("0.3");
    expect(op("idea-13")).toBe("0.3");
    expect(screen.getByTestId("citation")).toHaveAttribute("opacity", "0.3");
  });

  it("lights private documents", () => {
    render(<IdeaGraph graph={graph()} highlight={{ kind: "private" }} />);
    expect(op("node-3")).toBe("1");
    expect(op("node-1")).toBe("0.3");
  });

  it("wins over the selection while hovered, then gives it back", () => {
    const { rerender } = render(<IdeaGraph graph={graph()} selected={{ kind: "doc", id: 4 }} highlight={{ kind: "domains", ids: [1] }} />);
    expect(op("node-1")).toBe("1");
    expect(op("node-4")).toBe("0.3");
    rerender(<IdeaGraph graph={graph()} selected={{ kind: "doc", id: 4 }} highlight={null} />);
    expect(op("node-4")).toBe("1");
    expect(op("node-1")).toBe("0.3");
  });
});
