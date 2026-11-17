import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NoteEditorContext, type MarkProps } from "./markProps";
import { PaperEquationView, PaperFigureView } from "./RefViews";

function props(over: Partial<MarkProps>): MarkProps {
  return {
    markId: "mk_1",
    color: "",
    kind: "figure",
    quote: "",
    pieces: "[]",
    source: "{}",
    lang: "",
    orig: "",
    anchorLost: false,
    piecesLost: 0,
    ...over,
  };
}

function inNote(node: React.ReactNode) {
  return render(
    <NoteEditorContext.Provider value={{ noteId: 1, paperContentId: 3, paperTitle: "Duo" }}>{node}</NoteEditorContext.Provider>,
  );
}

describe("paper reference blocks for whole objects", () => {
  it("a figure marked in the HTML reader shows the figure's image", () => {
    inNote(
      <PaperFigureView
        id="b1"
        props={props({ quote: "Figure 1: Robot arm.", pieces: JSON.stringify([{ quote: "Figure 1: Robot arm.", figure_id: "fig-001" }]) })}
      />,
    );
    const img = screen.getByRole("img", { name: "Figure 1: Robot arm." });
    expect(img.getAttribute("src")).toContain("/papers/content/3/figures/fig-001/image");
  });

  it("a PDF figure shows its page crop", () => {
    inNote(
      <PaperFigureView
        id="b1"
        props={props({ quote: "Fig. 2", pieces: JSON.stringify([{ quote: "Fig. 2", page: 1, box: [0.1, 0.2, 0.9, 0.6] }]) })}
      />,
    );
    expect(screen.getByRole("img", { name: "Fig. 2" }).getAttribute("src")).toContain("/page-crop?page=1");
  });

  it("says the LaTeX was transcribed by the model only when it was", () => {
    const pdf = JSON.stringify([{ quote: "E", latex: "E = mc^2", page: 0, box: [0.1, 0.1, 0.5, 0.2] }]);
    const { unmount } = inNote(<PaperEquationView id="b1" props={props({ kind: "equation", pieces: pdf })} />);
    expect(screen.getByText(/transcribed by the model/)).toBeTruthy();
    unmount();
    const html = JSON.stringify([{ quote: "$E = mc^2$", latex: "E = mc^2", latex_from: "source" }]);
    inNote(<PaperEquationView id="b1" props={props({ kind: "equation", pieces: html })} />);
    expect(screen.queryByText(/transcribed by the model/)).toBeNull();
  });
});
