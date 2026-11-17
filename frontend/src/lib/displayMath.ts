import katex from "katex";

/** LaTeX as KaTeX can draw it: KaTeX has no `\label`, and a label means
 * nothing in a note (the stored LaTeX keeps it). */
export function displayLatex(latex: string): string {
  return latex.replace(/\\label\s*\{[^{}]*\}/g, "");
}

/** A note's display equation as KaTeX HTML ("" for no LaTeX). */
export function renderDisplayMath(latex: string): string {
  return latex ? katex.renderToString(displayLatex(latex), { displayMode: true, throwOnError: false }) : "";
}
