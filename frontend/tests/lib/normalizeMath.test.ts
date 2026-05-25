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

  it("lifts a chunk citation out of a $$ display block", () => {
    const out = normalizeMath("$$E = mc^2 [chunk:78081]$$");
    // The marker is gone from inside the fences and re-emitted after them.
    expect(out).toMatch(/\$\$[\s\S]*?\$\$\s*\[chunk:78081\]/);
    // The math body between the fences no longer carries the marker.
    const body = out.slice(out.indexOf("$$") + 2, out.lastIndexOf("$$"));
    expect(body).not.toContain("[chunk:");
  });

  it("lifts a chunk citation out of an equation environment", () => {
    const out = normalizeMath(
      "\\begin{equation}\nE = mc^2 [chunk:78081]\n\\end{equation}",
    );
    expect(out).toContain("[chunk:78081]");
    // The marker sits after \end{equation}, not before it.
    expect(out.indexOf("[chunk:78081]")).toBeGreaterThan(out.indexOf("\\end{equation}"));
  });

  it("lifts a chunk citation out of inline $ math", () => {
    const out = normalizeMath("the bound $f(x) [chunk:5]$ holds");
    expect(out).toContain("[chunk:5]");
    // Marker is outside the inline pair, not between the single $ delimiters.
    expect(out).not.toMatch(/\$[^$]*\[chunk:5\][^$]*\$/);
  });

  it("preserves a multi-id chunk marker when lifting it", () => {
    const out = normalizeMath("$$x = y [chunk:1, 2]$$");
    expect(out).toMatch(/\$\$[\s\S]*?\$\$\s*\[chunk:1, 2\]/);
  });

  it("drops empty math when a citation was its only content", () => {
    const out = normalizeMath("see $[chunk:9]$ here");
    expect(out).toContain("[chunk:9]");
    // No stray empty math fences left behind.
    expect(out).not.toContain("$$");
    expect(out).not.toMatch(/\$\s*\$/);
  });
});
