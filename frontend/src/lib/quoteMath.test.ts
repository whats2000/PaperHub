import { describe, expect, it } from "vitest";

import { quoteToKatexHtml } from "@/lib/quoteMath";

describe("quoteToKatexHtml", () => {
  it("draws inline $…$ math with KaTeX and escapes the text around it", () => {
    const html = quoteToKatexHtml(String.raw`over $\Theta$ <and> more`);
    expect(html).toContain('class="katex"');
    expect(html).toContain("&lt;and&gt;");
    expect(html).not.toContain("$");
  });

  it("draws an aligned environment as display math, without its label", () => {
    const html = quoteToKatexHtml(
      String.raw`$\begin{aligned} \max_{\Theta} \sum_{t=1}^{|y|} x \label{eq:ft_add} \end{aligned}$`,
    );
    expect(html).toContain("katex-display");
    expect(html).not.toContain("eq:ft_add");
    expect(html).not.toContain("katex-error");
  });

  it("leaves plain text (and an escaped dollar) as text", () => {
    expect(quoteToKatexHtml(String.raw`costs \$5 & \$6`)).toBe(String.raw`costs \$5 &amp; \$6`);
  });

  it("shows unparseable LaTeX as code, not a red error", () => {
    const html = quoteToKatexHtml(String.raw`see $\frac{1$ here`);
    expect(html).toContain("<code");
    expect(html).not.toContain("katex-error");
  });
});
