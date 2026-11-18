import katex from "katex";

import { displayLatex } from "@/lib/displayMath";

/** `$…$` in a quote from the paper's reading text (an escaped `\$` is a
 * dollar sign, not math). */
const MATH = /(?<!\\)\$((?:\\\$|[^$])+?)\$/g;
/** LaTeX that only reads as display math (a multi-line environment). */
const DISPLAY_ENV = /\\begin\s*\{(aligned|align\*?|equation\*?|gather\*?|multline\*?|array|cases|split)\}/;

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderMath(latex: string): string {
  try {
    return katex.renderToString(displayLatex(latex), {
      throwOnError: true,
      displayMode: DISPLAY_ENV.test(latex),
      output: "htmlAndMathml",
    });
  } catch {
    // Unparseable LaTeX: the source, not a red KaTeX error.
    return `<code class="font-mono text-[0.92em]">${escapeHtml(latex)}</code>`;
  }
}

/** HTML for a quote marked in a paper's HTML view: its `$…$` math drawn by
 * KaTeX (a multi-line environment as display math, `\label` dropped), the
 * text around it escaped. */
export function quoteToKatexHtml(text: string): string {
  let out = "";
  let last = 0;
  for (const m of text.matchAll(MATH)) {
    out += escapeHtml(text.slice(last, m.index));
    out += renderMath(m[1]!);
    last = m.index + m[0].length;
  }
  return out + escapeHtml(text.slice(last));
}
