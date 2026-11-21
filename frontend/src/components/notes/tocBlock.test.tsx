import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ATOM_CARET_TYPES } from "@/components/notes/atomCaret";
import { TocView } from "@/components/notes/blocks/TocView";
import { noteSchema } from "@/components/notes/schema";
import { cleanupEditors, makeEditor } from "@/components/notes/testEditor";
import { revealBlock } from "@/lib/notes/reveal";

vi.mock("@/lib/notes/reveal", () => ({
  revealBlock: vi.fn(() => Promise.resolve(true)),
  unfoldInNote: vi.fn(() => () => {}),
}));

const blockText = (b: { content: unknown }) => ((b.content as { text?: string }[])[0]?.text ?? "");

afterEach(() => {
  cleanupEditors();
  vi.clearAllMocks();
});

describe("toc block", () => {
  it("is in the schema, takes no text and takes the atom caret", () => {
    expect(noteSchema.blockSpecs.toc.config.content).toBe("none");
    expect(ATOM_CARET_TYPES.has("toc")).toBe(true);
    const editor = makeEditor();
    act(() => {
      editor.insertBlocks([{ type: "toc" }] as never, editor.document[0]!, "after");
    });
    expect(editor.document.some((b) => b.type === "toc")).toBe(true);
  });

  it("lists the headings live, indented by level; a click reveals; a rename shows", () => {
    const editor = makeEditor();
    render(<TocView editor={editor as never} />);
    expect(screen.getByText("Headings you add appear here.")).toBeInTheDocument();
    act(() => {
      editor.insertBlocks(
        [
          { type: "heading", props: { level: 1 }, content: "Intro" },
          { type: "heading", props: { level: 2 }, content: "Setup" },
        ] as never,
        editor.document[0]!,
        "after",
      );
    });
    const setup = screen.getByRole("button", { name: "Setup" });
    expect(screen.getByRole("button", { name: "Intro" })).toBeInTheDocument();
    expect(setup.parentElement!.style.paddingLeft).toBe("16px");
    const id = editor.document.find((b) => b.type === "heading" && blockText(b) === "Setup")!.id;
    fireEvent.click(setup);
    const [, calledId, opts] = vi.mocked(revealBlock).mock.calls[0]!;
    expect(calledId).toBe(id);
    expect(typeof opts?.unfold).toBe("function");
    act(() => {
      editor.updateBlock(id, { content: "Set up" });
    });
    expect(screen.getByRole("button", { name: "Set up" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Setup" })).toBeNull();
  });
});
