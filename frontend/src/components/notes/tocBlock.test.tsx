import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NodeSelection } from "@tiptap/pm/state";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ATOM_CARET_TYPES, atomCaret, setAtomCaret } from "@/components/notes/atomCaret";
import { TocView } from "@/components/notes/blocks/TocView";
import { noteSchema } from "@/components/notes/schema";
import { cleanupEditors, makeEditor, press } from "@/components/notes/testEditor";
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

  it("a note that opens on a table of contents does not open with it selected", async () => {
    const editor = makeEditor([atomCaret], [
      { id: "toc1", type: "toc" },
      { id: "a", type: "paragraph", content: "one" },
    ]);
    await Promise.resolve();
    const view = editor._tiptapEditor.view;
    expect(view.state.selection instanceof NodeSelection).toBe(false);
    expect(view.dom.querySelector(".ProseMirror-selectednode")).toBeNull();
  });

  it("takes no typing: a key at its caret opens a line beside it, and it can be removed", () => {
    const editor = makeEditor([atomCaret], [
      { id: "a", type: "paragraph", content: "one" },
      { id: "toc1", type: "toc" },
    ]);
    const view = editor._tiptapEditor.view;
    let pos = -1;
    view.state.doc.descendants((node, p) => {
      if (node.type.name === "toc") pos = p;
    });
    setAtomCaret(view, pos, "after");
    press(editor, { key: "x" });
    const types = editor.document.map((b) => b.type);
    expect(types.slice(0, 3)).toEqual(["paragraph", "toc", "paragraph"]);
    const toc = editor.document[1]!;
    expect(toc.content).toBeUndefined();
    expect((editor.document[2]!.content as { text: string }[])[0]!.text).toBe("x");
    editor.removeBlocks(["toc1"]);
    expect(editor.document.some((b) => b.type === "toc")).toBe(false);
  });

  it("animates entries: a removed one closes before it leaves", async () => {
    const editor = makeEditor([], [
      { id: "h", type: "heading", props: { level: 1 }, content: "Gone soon" },
      { id: "p", type: "paragraph", content: "x" },
    ]);
    render(<TocView editor={editor as never} />);
    const row = screen.getByRole("button", { name: "Gone soon" }).closest("li")!;
    expect(row.getAttribute("data-state")).toBe("open");
    act(() => {
      editor.removeBlocks(["h"]);
    });
    const leaving = screen.getByRole("button", { name: "Gone soon", hidden: true }).closest("li")!;
    expect(leaving.getAttribute("data-state")).toBe("out");
    expect(leaving.getAttribute("aria-hidden")).toBe("true");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Gone soon", hidden: true })).toBeNull());
    expect(screen.getByText("Headings you add appear here.")).toBeInTheDocument();
  });
});
