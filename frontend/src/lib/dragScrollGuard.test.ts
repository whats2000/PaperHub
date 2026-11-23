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
