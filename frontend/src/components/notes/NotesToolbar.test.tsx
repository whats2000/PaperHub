import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotesToolbar } from "@/components/notes/NotesToolbar";
import { useFollowReading } from "@/components/notes/useFollowReading";
import { useCanvasStore } from "@/store/canvas";
import { useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

function blk(id: string, type: string, extra: Partial<NoteBlock> = {}): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: type as NoteBlock["type"],
    props: {},
    content: null,
    text: "",
    author: "user",
    section_key: null,
    changed_rev: 1,
    ...extra,
  };
}

const s1 = blk("s1", "section", { props: { title: "1 Introduction" }, section_key: "1 Introduction" });
const s2 = blk("s2", "section", { props: { title: "2 Method" }, section_key: "2 Method" });

function seed() {
  useNotesStore.setState({
    notes: {
      7: {
        note: { id: 7, target_kind: "paper", target_id: "3", title: "T", revision: 9, updated_at: "" },
        blocks: [s1, blk("p1", "paragraph"), s2, blk("p2", "paragraph")],
        suggestions: [],
        comments: [],
        revision: 9,
      },
    },
    activePart: {},
    focusOn: false,
    followOn: true,
  });
}

function Toolbar(props: Partial<React.ComponentProps<typeof NotesToolbar>>) {
  const [guideOpen, setGuideOpen] = useState(false);
  return (
    <NotesToolbar
      noteId={7}
      followLabel="§2"
      sharedBy={2}
      guideOpen={guideOpen}
      onGuideOpenChange={setGuideOpen}
      {...props}
    />
  );
}

beforeEach(seed);
afterEach(() => {
  useCanvasStore.setState({ readingSection: null });
  vi.restoreAllMocks();
});

describe("NotesToolbar", () => {
  it("Follow names the followed section and turns off to 'Follow reading'", () => {
    render(<Toolbar />);
    const follow = screen.getByRole("button", { name: "Following §2" });
    expect(follow).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(follow);
    expect(useNotesStore.getState().followOn).toBe(false);
    expect(screen.getByRole("button", { name: "Follow reading" })).toHaveAttribute("aria-pressed", "false");
  });

  it("Follow is unavailable while the reader shows a PDF, and says why", () => {
    render(<Toolbar followUnavailable />);
    const follow = screen.getByRole("button", { name: "Follow reading" });
    expect(follow).toHaveAttribute("aria-disabled", "true");
    expect(follow).toHaveAttribute(
      "title",
      "Follow works on HTML papers for now; this paper is showing as a PDF.",
    );
    fireEvent.click(follow);
    expect(useNotesStore.getState().followOn).toBe(true);
  });

  it("hides Follow on a note with no paper (a chat's note), folding it away", () => {
    const { rerender } = render(<Toolbar />);
    expect(screen.getByRole("button", { name: "Following §2" })).toBeInTheDocument();
    rerender(<Toolbar followAvailable={false} />);
    expect(screen.queryByRole("button", { name: /Follow/ })).toBeNull();
    const slot = document.querySelector("[data-follow-slot]")!;
    expect(slot).toHaveAttribute("data-state", "closed");
    expect(slot).toHaveAttribute("inert");
    expect(slot.className).toMatch(/transition-\[grid-template-columns,opacity\]/);
  });

  it("Focus toggles, starts on the first part when none is chosen, and Esc stops it", () => {
    render(<Toolbar />);
    const focus = screen.getByRole("button", { name: "Focus" });
    expect(focus).toHaveAttribute("aria-pressed", "false");
    // The label hides in a narrow pane (a container query, not the viewport).
    expect(within(focus).getByText("Focus").className).toContain("@max-[380px]:hidden");
    fireEvent.click(focus);
    expect(useNotesStore.getState().focusOn).toBe(true);
    expect(useNotesStore.getState().activePart[7]).toBe("s1");
    expect(focus).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(useNotesStore.getState().focusOn).toBe(false);
  });

  it("the ⋯ menu shows the note's details, Export (coming soon until wired) and Shortcuts", async () => {
    render(<Toolbar />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const menu = await screen.findByRole("menu");
    expect(menu).toHaveTextContent("Shared by 2 chats · revision 9");
    const md = within(menu).getByRole("menuitem", { name: /Export as Markdown/ });
    const docx = within(menu).getByRole("menuitem", { name: /Export as Word/ });
    expect(md).toHaveAttribute("aria-disabled", "true");
    expect(docx).toHaveAttribute("aria-disabled", "true");
    expect(md).toHaveTextContent("Coming soon");
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Shortcuts" }));
    expect(await screen.findByRole("dialog", { name: "Shortcuts" })).toBeInTheDocument();
  });

  it("calls onExport with the format once it is wired", async () => {
    const onExport = vi.fn();
    render(<Toolbar onExport={onExport} sharedBy={null} />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const menu = await screen.findByRole("menu");
    expect(menu).toHaveTextContent("Revision 9");
    const docx = within(menu).getByRole("menuitem", { name: /Export as Word/ });
    expect(docx).not.toHaveAttribute("aria-disabled", "true");
    expect(docx).toHaveTextContent(".docx");
    fireEvent.click(docx);
    expect(onExport).toHaveBeenCalledWith("docx");
  });
});

describe("useFollowReading", () => {
  function mountNote() {
    const root = document.createElement("div");
    for (const id of ["s1", "p1", "s2", "p2"]) {
      const el = document.createElement("div");
      el.className = "bn-block-outer";
      el.dataset.id = id;
      root.appendChild(el);
    }
    document.body.appendChild(root);
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    return { root, scroll };
  }

  it("scrolls the note to the section the reader is at, and names it", async () => {
    const { root, scroll } = mountNote();
    const { result } = renderHook(() => useFollowReading({ noteId: 7, paperId: 3, root }));
    act(() => useCanvasStore.getState().setReadingSection(3, "2 Method"));
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
    expect(scroll.mock.instances[0]).toBe(root.querySelector('[data-id="s2"]'));
    expect(result.current).toBe("§2");
    // The followed section's part becomes the active one.
    expect(useNotesStore.getState().activePart[7]).toBe("s2");
    // Another paper's reader does not move this note.
    act(() => useCanvasStore.getState().setReadingSection(4, "1 Introduction"));
    expect(scroll).toHaveBeenCalledTimes(1);
    root.remove();
  });

  it("Follow off stops scrolling with the reader", () => {
    const { root, scroll } = mountNote();
    renderHook(() => useFollowReading({ noteId: 7, paperId: 3, root }));
    act(() => useNotesStore.getState().setFollowOn(false));
    act(() => useCanvasStore.getState().setReadingSection(3, "2 Method"));
    act(() => useCanvasStore.getState().setReadingSection(3, "1 Introduction"));
    expect(scroll).not.toHaveBeenCalled();
    // Turning it back on catches up with the reader.
    act(() => useNotesStore.getState().setFollowOn(true));
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.instances[0]).toBe(root.querySelector('[data-id="s1"]'));
    root.remove();
  });

  it("does not move the active part while Focus is on", () => {
    const { root } = mountNote();
    useNotesStore.setState({ focusOn: true, activePart: { 7: "s1" } });
    renderHook(() => useFollowReading({ noteId: 7, paperId: 3, root }));
    act(() => useCanvasStore.getState().setReadingSection(3, "2 Method"));
    expect(useNotesStore.getState().activePart[7]).toBe("s1");
    root.remove();
  });
});
