import { describe, expect, it } from "vitest";

import gutterCss from "./gutter.css?raw";

/** The rules of gutter.css whose selector mentions `needle`, as
 * [selector, body] pairs. */
function rules(needle: string): [string, string][] {
  const out: [string, string][] = [];
  for (const m of gutterCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const sel = m[1]!.replace(/\/\*[\s\S]*?\*\//g, "").trim();
    if (sel.includes(needle)) out.push([sel, m[2]!]);
  }
  return out;
}

describe("gutter.css", () => {
  it("puts an H1's toggle on its first line, H1 having no data-level (BlockNote leaves the default out)", () => {
    const h1 = rules(":not([data-level])");
    expect(h1).toHaveLength(1);
    expect(h1[0]![0]).toContain('[data-level="1"]');
    expect(h1[0]![1]).toMatch(/top:\s*42px/);
  });

  it("gives every gutter control one size: 16 x 24 with a 16 px icon at stroke 2", () => {
    const btn = rules(".note-gutter-btn").find(([s]) => s === ".note-gutter-btn")!;
    expect(btn[1]).toMatch(/width:\s*16px/);
    expect(btn[1]).toMatch(/height:\s*24px/);
    const icon = rules(".note-gutter-btn svg.lucide").find(([s]) => !s.startsWith("@"))!;
    expect(icon[1]).toMatch(/width:\s*16px/);
    expect(icon[1]).toMatch(/stroke-width:\s*2/);
  });

  it("moves BlockNote's side menu one toggle and a gap left, so + ⋮⋮ and the toggle never overlap", () => {
    const menu = rules(".bn-side-menu");
    expect(menu[0]![1]).toMatch(/translate:\s*-18px 0/);
    const host = rules(".note-fold-host").find(([s]) => s === ".paperhub-notes .note-fold-host")!;
    expect(host[1]).toMatch(/left:\s*-16px/);
  });
});
