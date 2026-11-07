import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";

import { rehypeTableBreaks } from "@/lib/rehypeTableBreaks";

const md = (src: string) =>
  render(
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeTableBreaks]}>
      {src}
    </ReactMarkdown>,
  ).container;

describe("rehypeTableBreaks", () => {
  it("turns <br> variants inside a table cell into line breaks", () => {
    const c = md("| a | b |\n|---|---|\n| one<br/>two<br>three<BR />four | x |");
    const cell = c.querySelector("tbody td");
    expect(cell?.querySelectorAll("br")).toHaveLength(3);
    expect(cell?.textContent).toBe("onetwothreefour");
    expect(c.textContent).not.toContain("<br");
  });

  it("leaves <br> outside a table as literal text and never injects other HTML", () => {
    const c = md("para<br/>text\n\n| a |\n|---|\n| <img src=x onerror=alert(1)><br/>y |");
    expect(c.querySelector("p")?.textContent).toContain("<br/>");
    expect(c.querySelector("img")).toBeNull();
    expect(c.querySelectorAll("td br")).toHaveLength(1);
    expect(c.querySelector("td")?.textContent).toContain("<img");
  });
});
