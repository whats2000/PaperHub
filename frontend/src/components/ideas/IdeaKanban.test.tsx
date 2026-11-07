import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { IdeaFilter } from "@/store/ideation";
import type { GraphResponse, Idea, IdeaStatus } from "@/types/ideation";

import { IdeaKanban } from "./IdeaKanban";

const idea = (id: number, status: IdeaStatus, over: Partial<Idea> = {}): Idea => ({
  id,
  problem_id: null,
  source_doc: 1,
  source_section: "RPN",
  source_chunk_ids: [10],
  target_doc: 2,
  target_section: "Prompting",
  target_chunk_ids: [20],
  title: `Idea ${id}`,
  rationale: "",
  status,
  prev_status: null,
  note: "",
  dismiss_reasons: [],
  session_ids: [],
  created_at: "2026-11-05T10:00:00Z",
  ...over,
});

const IDEAS = [
  idea(1, "new"),
  idea(2, "saved", { note: "Ask the lab" }),
  idea(3, "trying", { session_ids: [41, 42] }),
  idea(4, "worked"),
  idea(5, "failed"),
  idea(6, "dismissed", { dismiss_reasons: ["analogy_fails", "not_novel"] }),
];

const graph = (ideas: Idea[], problem: GraphResponse["problem"] = null): GraphResponse => ({
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
  ideas,
  problem,
});

function renderKanban(filter: IdeaFilter, ideas = IDEAS, problem: GraphResponse["problem"] = null) {
  const onSelect = vi.fn();
  const g = graph(ideas, problem);
  const props = {
    graph: g,
    // The whole scope: the board does its own filtering.
    ideas,
    filter,
    selected: null,
    onSelect,
  };
  const view = render(<IdeaKanban {...props} />);
  return { ...view, onSelect, props };
}

const column = (name: string) => screen.getByRole("region", { name });
const cardIds = (name: string) =>
  within(column(name))
    .queryAllByTestId(/^idea-card-/)
    .map((el) => Number(el.dataset.testid?.replace("idea-card-", "")));

describe("IdeaKanban", () => {
  it("puts ideas in the new · saved · trying · has result columns and hides dismissed ones", () => {
    renderKanban("active");
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"))).toEqual([
      "New",
      "Saved",
      "Trying",
      "Has result",
    ]);
    expect(cardIds("New")).toEqual([1]);
    expect(cardIds("Saved")).toEqual([2]);
    expect(cardIds("Trying")).toEqual([3]);
    expect(cardIds("Has result")).toEqual([4, 5]);
    expect(screen.queryByTestId("idea-card-6")).toBeNull();
  });

  it("filters by itself: saved shows only saved ideas, never dismissed ones", () => {
    renderKanban("saved");
    expect(screen.queryAllByTestId(/^idea-card-/).map((el) => el.dataset.testid)).toEqual(["idea-card-2"]);
  });

  it("shows dismissed ideas only under their filter, with the reasons", () => {
    renderKanban("dismissed");
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"))).toEqual(["Dismissed"]);
    const card = screen.getByTestId("idea-card-6");
    expect(card).toHaveTextContent("Dismissed: Analogy doesn't hold, Not novel");
  });

  it("cards show the status, note and chat count, and select the idea", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderKanban("active");
    expect(screen.getByTestId("idea-card-2")).toHaveTextContent("Ask the lab");
    expect(screen.getByTestId("idea-card-3")).toHaveTextContent("Trying");
    expect(screen.getByTestId("idea-card-3")).toHaveTextContent("2 chats");
    expect(screen.getByTestId("idea-card-5")).toHaveTextContent("Failed");
    await user.click(screen.getByTestId("idea-card-4"));
    expect(onSelect).toHaveBeenCalledWith({ kind: "idea", id: 4 });
  });

  it("matches the scope: a problem's text, or free exploration", () => {
    const { unmount } = renderKanban("active");
    expect(screen.getByText("Free exploration: cross-domain pairs within your library")).toBeInTheDocument();
    unmount();
    renderKanban("active", [idea(1, "new", { target_doc: null, target_section: null, target_chunk_ids: [] })], {
      id: 7,
      text: "Rerank retrieved images",
      tried: "",
      created_at: "2026-11-05T10:00:00Z",
      idea_count: 1,
      status: "ready",
    });
    expect(screen.getByText("For your problem: Rerank retrieved images")).toBeInTheDocument();
    expect(screen.getByTestId("idea-card-1")).toHaveTextContent("Your problem");
  });

  it("a card moving to another column fades out of the old one", async () => {
    const { rerender, props } = renderKanban("active");
    const moved = IDEAS.map((i) => (i.id === 2 ? { ...i, status: "trying" as const } : i));
    rerender(<IdeaKanban {...props} ideas={moved} />);
    // Arrives in Trying, and lingers in Saved as a leaving ghost.
    expect(cardIds("Trying")).toEqual([2, 3]);
    const ghost = within(column("Saved")).getByTestId("idea-card-2");
    expect(ghost).toHaveAttribute("data-leaving", "true");
    expect(ghost.className).toContain("fade-out-0");
    await waitFor(() => expect(within(column("Saved")).queryByTestId("idea-card-2")).toBeNull());
  });

  it("keeps room for the drawer and scrolls the selected card clear of it", () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const { props, rerender } = renderKanban("active");
    rerender(<IdeaKanban {...props} selected={{ kind: "idea", id: 3 }} rightInset={340} />);
    expect(screen.getByTestId("idea-kanban").style.paddingRight).toBe("360px");
    expect(scroll).toHaveBeenCalledWith(expect.objectContaining({ inline: "nearest" }));
    // Columns can shrink so four fit beside the drawer.
    expect(column("New")).toHaveClass("min-w-[168px]");
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });
});
