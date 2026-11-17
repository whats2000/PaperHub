import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MyMarksMenu } from "@/components/canvas/MyMarksMenu";
import { patchMarkColor } from "@/lib/api";
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
