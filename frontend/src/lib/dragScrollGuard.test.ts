import { afterEach, describe, expect, it } from "vitest";

import indexCss from "@/index.css?raw";

import { installDragScrollGuard } from "./dragScrollGuard";

let uninstall: (() => void) | undefined;
afterEach(() => {
  uninstall?.();
  uninstall = undefined;
  document.documentElement.removeAttribute("data-dragging");
});

const root = () => document.documentElement;
const fire = (type: string, target: EventTarget = document.body) =>
  target.dispatchEvent(new Event(type, { bubbles: true }));

describe("installDragScrollGuard", () => {
  it("marks the page while a drag is in progress", () => {
    uninstall = installDragScrollGuard(document);
    fire("dragstart");
    expect(root().hasAttribute("data-dragging")).toBe(true);
    fire("dragend");
    expect(root().hasAttribute("data-dragging")).toBe(false);
  });

  it("clears on drop", () => {
    uninstall = installDragScrollGuard(document);
    fire("dragstart");
    fire("drop");
    expect(root().hasAttribute("data-dragging")).toBe(false);
  });

  // A drag whose source is removed mid-drag sends no dragend to the
  // document; the first pointer move after the drag ends clears it.
  it("clears on the next pointer move when dragend never arrives", () => {
    uninstall = installDragScrollGuard(document);
    fire("dragstart");
    fire("pointermove");
    expect(root().hasAttribute("data-dragging")).toBe(false);
  });

  it("stops listening once uninstalled", () => {
    installDragScrollGuard(document)();
    fire("dragstart");
    expect(root().hasAttribute("data-dragging")).toBe(false);
  });

  it("clips the page's overflow only while marked", () => {
    const css = indexCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(css).toMatch(/html\[data-dragging\]\s*\{\s*overflow:\s*hidden;/);
    expect(css).not.toMatch(/html\s*\{[^}]*overflow/);
  });
});

describe("installDragScrollGuard with a drag image in <body>", () => {
  // BlockNote appends its drag image on dragstart and removes it on the
  // handle's dragend, which for a row comes ~100ms after the drop (the
  // handle moves with its row): the mark must outlast the image.
  it("keeps the mark after a drop until the drag image leaves <body>", async () => {
    uninstall = installDragScrollGuard(document);
    fire("dragstart");
    const img = document.createElement("div");
    document.body.appendChild(img);
    fire("drop");
    fire("pointermove");
    expect(root().hasAttribute("data-dragging")).toBe(true);
    img.remove();
    await new Promise((r) => setTimeout(r, 0));
    expect(root().hasAttribute("data-dragging")).toBe(false);
  });

  it("clears after a timeout if the image is never removed", async () => {
    uninstall = installDragScrollGuard(document, { settleMs: 20 });
    fire("dragstart");
    const img = document.createElement("div");
    document.body.appendChild(img);
    fire("dragend");
    expect(root().hasAttribute("data-dragging")).toBe(true);
    await new Promise((r) => setTimeout(r, 40));
    expect(root().hasAttribute("data-dragging")).toBe(false);
    img.remove();
  });
});
