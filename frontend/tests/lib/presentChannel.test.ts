import { describe, expect, it } from "vitest";
import { createPresentChannel } from "@/lib/presentChannel";

describe("presentChannel", () => {
  it("broadcasts page changes presenter → audience", () => {
    const presenter = createPresentChannel(7);
    const audience = createPresentChannel(7);
    const pages: number[] = [];
    audience.onPage((p) => pages.push(p));
    presenter.postPage(3);
    expect(pages).toEqual([3]);
    presenter.close();
    audience.close();
  });

  it("ping → pong round-trips for the heartbeat", () => {
    const presenter = createPresentChannel(9);
    const audience = createPresentChannel(9);
    let pongs = 0;
    presenter.onPong(() => (pongs += 1));
    audience.onPing(() => audience.pong());
    presenter.ping();
    expect(pongs).toBe(1);
    presenter.close();
    audience.close();
  });

  it("does not deliver a channel's own messages to itself", () => {
    const a = createPresentChannel(1);
    let seen = false;
    a.onPage(() => (seen = true));
    a.postPage(2);
    expect(seen).toBe(false);
    a.close();
  });
});
