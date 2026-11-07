import katex from "katex";

/** `⟦n⟧` (protected inline content) or `⟦BR⟧` (page/column break). */
const PLACEHOLDER = /⟦(\d+|BR)⟧/g;

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
    return katex.renderToString(latex, {
      throwOnError: true,
      displayMode: false,
      output: "htmlAndMathml",
    });
  } catch {
    // Unparseable LaTeX: show the source rather than a red KaTeX error.
    return `<code class="font-mono text-[0.92em]">${escapeHtml(latex)}</code>`;
  }
}

/**
 * HTML for a translated PDF paragraph: the text is escaped, every `⟦n⟧` is
 * rendered by KaTeX from `math[n]` (LaTeX: inline math, or a reference marker
 * such as `\text{[3]}`), and the i-th `⟦BR⟧` becomes a thin divider where the
 * paragraph continues on the next column or page, labelled `breakLabels[i]`
 * ("continued on page 3"); an `undefined` label draws a plain divider. An
 * index with no math stays literal.
 */
export function mathToKatexHtml(
  text: string,
  math: readonly string[],
  breakLabels: readonly (string | undefined)[] = [],
): string {
  let out = "";
  let last = 0;
  let breaks = 0;
  for (const m of text.matchAll(PLACEHOLDER)) {
    const at = m.index;
    out += escapeHtml(text.slice(last, at));
    const token = m[1]!;
    if (token === "BR") {
      const label = breakLabels[breaks++];
      out +=
        label === undefined
          ? '<span data-break="" class="my-2 block border-t border-border" aria-hidden="true"></span>'
          : `<span data-break="" class="my-2 block border-t border-border pt-1 text-center text-[11.5px] text-muted-foreground">${escapeHtml(label)}</span>`;
    } else {
      const latex = math[Number(token)];
      out += latex === undefined ? escapeHtml(m[0]) : renderMath(latex);
    }
    last = at + m[0].length;
  }
  return out + escapeHtml(text.slice(last));
}
