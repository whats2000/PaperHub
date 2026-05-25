import { describe, expect, it } from "vitest";

import { normalizeMath } from "@/lib/normalizeMath";

describe("normalizeMath", () => {
  it("wraps a bare equation environment in $$ so remark-math detects it", () => {
    const out = normalizeMath("Here:\n\\begin{equation}\nE = mc^2\n\\end{equation}\nDone.");
    expect(out).toContain("$$");
    expect(out).toContain("\\begin{equation}");
    expect(out).toContain("\\end{equation}");
    // The environment must sit between $$ fences.
    expect(out).toMatch(/\$\$[\s\S]*\\begin\{equation\}[\s\S]*\\end\{equation\}[\s\S]*\$\$/);
  });

  it("preserves the starred variant", () => {
    const out = normalizeMath("\\begin{equation*}\na=b\n\\end{equation*}");
    expect(out).toContain("\\begin{equation*}");
    expect(out).toContain("\\end{equation*}");
    expect(out).toMatch(/\$\$/);
  });

  it("wraps align and gather environments", () => {
    expect(normalizeMath("\\begin{align}a&=b\\end{align}")).toMatch(/\$\$[\s\S]*align[\s\S]*\$\$/);
    expect(normalizeMath("\\begin{gather}a=b\\end{gather}")).toMatch(/\$\$[\s\S]*gather[\s\S]*\$\$/);
  });

  it("does not double-wrap an environment already inside $$", () => {
    const out = normalizeMath("$$\n\\begin{equation}\nx=1\n\\end{equation}\n$$");
    expect(out).not.toContain("$$$$");
    expect(out).not.toMatch(/\$\$\s*\$\$/);
    // Exactly one opening and one closing fence.
    expect(out.match(/\$\$/g)?.length).toBe(2);
  });

  it("converts \\[ ... \\] display delimiters to $$", () => {
    const out = normalizeMath("Loss: \\[ \\sum_i x_i \\]");
    expect(out).toContain("$$");
    expect(out).not.toContain("\\[");
    expect(out).not.toContain("\\]");
  });

  it("converts \\( ... \\) inline delimiters to single $", () => {
    const out = normalizeMath("the value \\( x^2 \\) is small");
    expect(out).not.toContain("\\(");
    expect(out).not.toContain("\\)");
    // A single-$ inline pair around the body (not the $$ display fence).
    expect(out).toMatch(/(^|[^$])\$\s*x\^2\s*\$([^$]|$)/);
  });

  it("leaves a string with no LaTeX environments unchanged", () => {
    const text = "Just a normal sentence with $a + b$ inline math.";
    expect(normalizeMath(text)).toBe(text);
  });

  it("does not touch LaTeX inside fenced code blocks", () => {
    const text = "```latex\n\\begin{equation}\nx=1\n\\end{equation}\n```";
    expect(normalizeMath(text)).toBe(text);
  });

  it("does not touch LaTeX inside inline code spans", () => {
    const text = "Use `\\begin{equation}` to start an equation.";
    expect(normalizeMath(text)).toBe(text);
  });
});
