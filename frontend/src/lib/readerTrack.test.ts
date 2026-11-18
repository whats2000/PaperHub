import { afterEach, describe, expect, it } from "vitest";

import { readerTrackSliding } from "@/lib/readerTrack";

afterEach(() => {
  document.body.innerHTML = "";
});

function track(states: string[]): void {
  const el = document.createElement("div");
  el.setAttribute("data-reader-track", "");
  (el as unknown as { getAnimations: () => { playState: string }[] }).getAnimations = () =>
    states.map((playState) => ({ playState }));
  document.body.appendChild(el);
}

describe("readerTrackSliding", () => {
  it("is true while the HTML/PDF track's slide runs", () => {
    track(["running"]);
    expect(readerTrackSliding()).toBe(true);
  });
  it("is false once the slide has finished", () => {
    track(["finished"]);
    expect(readerTrackSliding()).toBe(false);
  });
  it("is false with no track", () => {
    expect(readerTrackSliding()).toBe(false);
  });
});
