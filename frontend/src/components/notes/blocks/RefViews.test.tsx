import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NoteEditorContext, type MarkProps } from "./markProps";
import { PaperEquationView, PaperFigureView, PaperQuoteView } from "./RefViews";

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
    sourceRef: "",
    translationChanged: false,
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

  it("draws a source equation without its \\label, which KaTeX would print as text", () => {
    const latex = String.raw`k = \frac{n \times c}{e} \label{eq:es}`;
    const pieces = JSON.stringify([{ quote: `$${latex}$`, latex, latex_from: "source" }]);
    const { container } = inNote(<PaperEquationView id="b1" props={props({ kind: "equation", pieces })} />);
    const math = container.querySelector(".katex")!;
    expect(math.textContent).not.toContain("label");
    expect(math.textContent).not.toContain("eq:es");
    expect(container.querySelector(".katex-error")).toBeNull();
  });
});

describe("a highlight of several pieces", () => {
  it("shows one quote in reading order with … between the pieces and the piece count", () => {
    inNote(
      <PaperQuoteView
        id="b1"
        props={props({
          kind: "highlight",
          color: "green",
          quote: "agent pose of time",
          // Drawn later piece first: shown in the paper's order.
          pieces: JSON.stringify([
            { quote: "of time", chunk_start: 2, text_start: 30 },
            { quote: "agent pose", chunk_start: 2, text_start: 5 },
          ]),
        })}
      />,
    );
    expect(screen.getByText("agent pose … of time")).toBeTruthy();
    expect(screen.getByText(/2 pieces/)).toBeTruthy();
  });
});

describe("a highlight of one selection across paragraphs", () => {
  it("shows the paragraphs on their own lines, with no …", () => {
    inNote(
      <PaperQuoteView
        id="b2"
        props={props({
          kind: "highlight",
          color: "green",
          quote: "end of one start of two",
          pieces: JSON.stringify([
            { quote: "end of one", chunk_start: 1, stroke: 0 },
            { quote: "start of two", chunk_start: 2, stroke: 0 },
          ]),
        })}
      />,
    );
    const quote = screen.getByText((_, el) => el?.tagName === "BLOCKQUOTE");
    expect(quote.textContent).toBe("end of one\nstart of two");
    expect(quote.className).toContain("whitespace-pre-line");
  });
});


describe("a quote marked on a translation", () => {
  const tr = {
    kind: "highlight",
    color: "purple",
    quote: "預測代理的姿態",
    lang: "繁體中文",
    orig: "We predict the agent pose ahead of time.",
    sourceRef: "h:3",
    pieces: JSON.stringify([{ quote: "預測代理的姿態", lang: "繁體中文", source_ref: "h:3" }]),
  };

  it("is tagged as translated, with Show original", () => {
    inNote(<PaperQuoteView id="b1" props={props(tr)} />);
    expect(screen.getByText(/Translated · 繁中/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Show original/ })).toBeTruthy();
    expect(screen.getByTestId("orig-b1").textContent).toContain("We predict the agent pose ahead of time.");
    expect(screen.queryByTestId("translation-changed-b1")).toBeNull();
  });

  it("keeps its quote and says so when the translation changed", () => {
    inNote(<PaperQuoteView id="b1" props={props({ ...tr, translationChanged: true })} />);
    expect(screen.getByTestId("translation-changed-b1").textContent).toContain("no longer in the 繁中 translation");
    expect(screen.getByText("預測代理的姿態")).toBeTruthy();
  });
});
