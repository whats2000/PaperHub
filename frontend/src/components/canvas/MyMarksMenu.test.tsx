import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MyMarksMenu } from "@/components/canvas/MyMarksMenu";
import { patchMarkColor } from "@/lib/api";
import { chunkMap } from "@/lib/marks/anchor";
import { markPositions } from "@/lib/marks/pieces";
import { useCanvasStore } from "@/store/canvas";
import { useMarksStore } from "@/store/marks";
import { useNotesStore } from "@/store/notes";
import { useTranslationStore } from "@/store/translation";
import type { Mark, MarkColorInfo } from "@/types/notes";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  patchMarkColor: vi.fn(),
}));
const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(toastFn, { error: vi.fn(), message: vi.fn(), success: vi.fn(), dismiss: vi.fn() }),
}));

const COLORS: MarkColorInfo[] = [
  { color: "green", name: "Key result", visible: true },
  { color: "blue", name: "Method", visible: true },
  { color: "pink", name: "Question", visible: true },
  { color: "purple", name: "Disagree", visible: true },
];

function mark(id: string, section: string, chunk: number, over: Partial<Mark> = {}): Mark {
  return {
    id,
    paper_content_id: 3,
    block_id: `b_${id}`,
    kind: "highlight",
    color: "green",
    view: "html",
    pieces: [{ quote: `quote ${id}`, chunk_start: chunk, chunk_end: chunk }],
    lang: null,
    source_ref: null,
    section_key: section,
    status: "ok",
    created_at: "2026-11-16T00:00:00Z",
    deleted_at: null,
    note_id: 7,
    part_label: section,
    ...over,
  };
}

beforeEach(() => {
  vi.mocked(patchMarkColor).mockReset();
  useNotesStore.setState({ colorNames: {} });
  useMarksStore.setState({
    colors: COLORS,
    // Saved in a different order from the paper's.
    marksByPaper: {
      3: [
        mark("m3", "3 Results", 30, { color: "blue" }),
        mark("m1", "2 Method", 12, { part_label: "2 Method · 2" }),
        mark("m2", "2 Method", 10, { kind: "quote", color: null }),
      ],
    },
  });
});

async function openMenu(count = 3) {
  render(<MyMarksMenu paperId={3} shown />);
  expect(screen.getByTestId("my-marks-count")).toHaveTextContent(String(count));
  fireEvent.click(screen.getByRole("button", { name: /My marks/ }));
  return screen.findByRole("dialog", { name: "My marks" });
}

describe("My marks", () => {
  it("lists the marks by section in reading order, with counts and the note they are in", async () => {
    const menu = await openMenu();
    const sections = within(menu).getAllByRole("region");
    expect(sections.map((s) => s.getAttribute("aria-label"))).toEqual(["§2", "§3"]);
    const rows = within(sections[0]!).getAllByRole("button");
    expect(rows.map((r) => r.dataset.testid)).toEqual(["my-mark-m2", "my-mark-m1"]);
    expect(within(sections[0]!).getByRole("heading")).toHaveTextContent("§2 Method2");
    expect(rows[1]).toHaveTextContent("note 2");
    // Each colour's count.
    const green = within(menu).getByDisplayValue("Key result").parentElement!;
    expect(green).toHaveTextContent("1");
  });

  it("renames a colour in place and keeps it", async () => {
    vi.mocked(patchMarkColor).mockResolvedValue(
      COLORS.map((c) => (c.color === "green" ? { ...c, name: "Findings" } : c)),
    );
    const menu = await openMenu();
    const input = within(menu).getByDisplayValue("Key result");
    fireEvent.change(input, { target: { value: "Findings" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(patchMarkColor).toHaveBeenCalledWith("green", { name: "Findings" });
    await waitFor(() => expect(useNotesStore.getState().colorNames.green).toBe("Findings"));
    expect(within(menu).getByDisplayValue("Findings")).toBeInTheDocument();
  });

  it("puts a name back when the server refuses it", async () => {
    vi.mocked(patchMarkColor).mockRejectedValue(new Error("API 500"));
    const menu = await openMenu();
    const input = within(menu).getByDisplayValue("Method");
    fireEvent.change(input, { target: { value: "Ours" } });
    fireEvent.blur(input);
    await waitFor(() => expect(useMarksStore.getState().colors.find((c) => c.color === "blue")!.name).toBe("Method"));
    expect(within(menu).getByDisplayValue("Method")).toBeInTheDocument();
  });

  it("shows and hides a colour's marks", async () => {
    vi.mocked(patchMarkColor).mockImplementation((color, body) =>
      Promise.resolve(COLORS.map((c) => (c.color === color ? { ...c, ...body } : c))),
    );
    const menu = await openMenu();
    const show = within(menu).getByRole("checkbox", { name: "Show Method" });
    expect(show).toBeChecked();
    fireEvent.click(show);
    expect(patchMarkColor).toHaveBeenCalledWith("blue", { visible: false });
    expect(useMarksStore.getState().colors.find((c) => c.color === "blue")!.visible).toBe(false);
    await waitFor(() => expect(within(menu).getByRole("checkbox", { name: "Show Method" })).not.toBeChecked());
  });

  it("jumps to a mark: the reader opens at it in its view and flashes it", async () => {
    const openPaper = vi.spyOn(useCanvasStore.getState(), "openPaper");
    const tab = vi.spyOn(useCanvasStore.getState(), "requestReaderTab");
    const reveal = vi.spyOn(useTranslationStore.getState(), "requestReveal");
    useMarksStore.setState({
      marksByPaper: { 3: [mark("m1", "2 Method", 12, { view: "pdf", pieces: [{ quote: "on a page", page: 2 }] })] },
    });
    const menu = await openMenu(1);
    fireEvent.click(within(menu).getByTestId("my-mark-m1"));
    await waitFor(() => expect(reveal).toHaveBeenCalledWith(3, "m1", { fallback: true }));
    expect(openPaper).toHaveBeenCalledWith(3);
    expect(tab).toHaveBeenCalledWith(3, "pdf");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "My marks" })).toBeNull());
    openPaper.mockRestore();
    tab.mockRestore();
    reveal.mockRestore();
  });
});

describe("My marks, fix round 1", () => {
  it("sends a rename once, on Enter (which leaves the field) or on leaving it", async () => {
    vi.mocked(patchMarkColor).mockResolvedValue(COLORS);
    const menu = await openMenu();
    const input = within(menu).getByDisplayValue("Key result");
    fireEvent.change(input, { target: { value: "Findings" } });
    input.focus();
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    expect(patchMarkColor).toHaveBeenCalledTimes(1);
    const other = within(menu).getByDisplayValue("Method");
    fireEvent.change(other, { target: { value: "Ours" } });
    fireEvent.blur(other);
    fireEvent.blur(other);
    expect(patchMarkColor).toHaveBeenCalledTimes(2);
  });

  it("orders marks anchored to the text and marks only on a page together, as the paper reads", async () => {
    useMarksStore.setState({
      // §1 holds chunks 1-2, §7 chunk 9.
      chunksByPaper: {
        3: chunkMap([
          { id: 1, dom_id: "phchunk-0", section: "1 Intro" },
          { id: 2, dom_id: "phchunk-1", section: "1 Intro" },
          { id: 9, dom_id: "phchunk-2", section: "7 Discussion" },
        ]),
      },
      marksByPaper: {
        3: [
          mark("late", "7 Discussion", 9),
          // A figure boxed on page 2, in §1: no chunk.
          mark("fig", "1 Intro", 0, {
            kind: "figure",
            view: "pdf",
            pieces: [{ quote: "Figure 1", page: 1, box: [0.1, 0.3, 0.9, 0.6] }],
          }),
          mark("early", "1 Intro", 2),
        ],
      },
    });
    const menu = await openMenu();
    const sections = within(menu).getAllByRole("region");
    expect(sections.map((s) => s.getAttribute("aria-label"))).toEqual(["§1", "§7"]);
    expect(within(sections[0]!).getAllByRole("button").map((r) => r.dataset.testid)).toEqual([
      "my-mark-fig",
      "my-mark-early",
    ]);
  });

  it("places a page-only mark between text marks through a mark that has both", () => {
    const chunks = [1, 2, 3, 4].map((id) => ({ id, section: "2 Method" }));
    const located = mark("located", "2 Method", 3, {
      view: "pdf",
      pieces: [{ quote: "located", page: 4, rects: [[0.1, 0.2, 0.5, 0.22]], chunk_start: 3, chunk_end: 3 }],
    });
    const box = mark("box", "2 Method", 0, {
      kind: "region",
      view: "pdf",
      pieces: [{ quote: "region", page: 5, box: [0.1, 0.5, 0.5, 0.6] }],
    });
    const first = mark("first", "2 Method", 1);
    const last = mark("last", "2 Method", 4);
    const pos = markPositions([last, box, located, first], chunks);
    const order = ["first", "located", "box", "last"].map((id) => pos.get(id)!);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("jumps to a mark whose colour is hidden: shows it for now, with Hide again", async () => {
    const reveal = vi.spyOn(useTranslationStore.getState(), "requestReveal");
    useMarksStore.setState({
      colors: COLORS.map((c) => (c.color === "blue" ? { ...c, visible: false } : c)),
      marksByPaper: { 3: [mark("m3", "3 Results", 30, { color: "blue" })] },
    });
    const menu = await openMenu(1);
    fireEvent.click(within(menu).getByTestId("my-mark-m3"));
    await waitFor(() => expect(reveal).toHaveBeenCalledWith(3, "m3", { fallback: true }));
    expect(useMarksStore.getState().colors.find((c) => c.color === "blue")!.visible).toBe(true);
    // Nothing is saved.
    expect(patchMarkColor).not.toHaveBeenCalled();
    const call = toastFn.mock.calls.find((c) => c[0] === "Showing a hidden colour")!;
    const hide = (call[1] as { action: { label: string; onClick: () => void } }).action;
    expect(hide.label).toBe("Hide again");
    hide.onClick();
    expect(useMarksStore.getState().colors.find((c) => c.color === "blue")!.visible).toBe(false);
    reveal.mockRestore();
  });

  it("lists by kind, each entry with its kind, a snippet and its note", async () => {
    useMarksStore.setState({
      marksByPaper: {
        3: [
          mark("h1", "2 Method", 10),
          mark("f1", "2 Method", 11, { kind: "figure", part_label: "2 Method · 2" }),
        ],
      },
    });
    const menu = await openMenu(2);
    const fig = within(menu).getByTestId("my-mark-f1");
    expect(within(fig).getByRole("img", { name: "Figure" })).toBeInTheDocument();
    expect(fig).toHaveTextContent("quote f1");
    expect(fig).toHaveTextContent("note 2");
    expect(within(menu).getByTestId("my-mark-h1")).toHaveTextContent("note 1");
    const kinds = within(menu).getByRole("group", { name: "Kinds to list" });
    fireEvent.click(within(kinds).getByRole("button", { name: "Figure" }));
    expect(within(kinds).getByRole("button", { name: "Figure" })).toHaveAttribute("aria-pressed", "false");
    expect(within(menu).queryByTestId("my-mark-f1")).toBeNull();
    expect(within(menu).getByTestId("my-mark-h1")).toBeInTheDocument();
    expect(kinds).toHaveTextContent("1 of 2");
    // The trigger still counts every mark on the paper.
    expect(screen.getByTestId("my-marks-count")).toHaveTextContent("2");
  });
});
