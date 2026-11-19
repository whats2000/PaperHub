import { afterEach, describe, expect, it } from "vitest";
import { injectPerfStyle } from "@/lib/withBaseHref";

// jsdom cascades style sheets only in the window's own document.
function applyPerf(): HTMLStyleElement {
  const perf = document.createElement("style");
  perf.textContent = injectPerfStyle("<head></head>").match(/<style>(.*)<\/style>/s)![1]!;
  document.head.appendChild(perf);
  return perf;
}

describe("injectPerfStyle: display math", () => {
  afterEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
  });

  // `content-visibility: auto` on each top-level block contains paint, so a
  // display equation wider than its paragraph was cut at the paragraph's
  // edge with no way to reach the rest. Each display equation scrolls
  // sideways inside its own box instead.
  it("lets a MathJax display equation scroll sideways in its own box", () => {
    applyPerf();
    document.body.innerHTML =
      '<p><span class="math display"><mjx-container jax="CHTML" display="true">x</mjx-container></span></p>' +
      '<p><span class="math inline"><mjx-container jax="CHTML">y</mjx-container></span></p>';
    const [display, inline] = Array.from(document.querySelectorAll("mjx-container"));
    const s = getComputedStyle(display!);
    expect(s.overflowX).toBe("auto");
    expect(s.display).toBe("block");
    expect(s.maxWidth).toBe("100%");
    expect(getComputedStyle(inline!).overflowX).not.toBe("auto");
  });

  it("does the same for native MathML block math", () => {
    // jsdom computes no style for MathML elements: check the rule's selector.
    const css = injectPerfStyle("<head></head>");
    expect(css).toMatch(/math\[display="block"\][^{]*\{[^}]*overflow-x: auto/);
  });
});
