import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MathView } from "./MathView";

describe("MathView", () => {
  it("draws an equation copied from a paper without its \\label, which KaTeX would print as text", () => {
    const latex = String.raw`k = \frac{n \times c}{e} \label{eq:es}`;
    const { container } = render(<MathView latex={latex} onChange={() => undefined} />);
    const math = container.querySelector(".katex")!;
    expect(math.textContent).not.toContain("label");
    expect(math.textContent).not.toContain("eq:es");
  });
});
