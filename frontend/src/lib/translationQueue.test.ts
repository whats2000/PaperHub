import { describe, expect, it } from "vitest";
import { createTranslationQueue } from "@/lib/translationQueue";
import type { TranslateRequest, TranslateResponse, TranslateResult } from "@/types/translation";

interface Call {
  req: TranslateRequest;
  resolve: () => void;
}

function setup(n: number, opts: { fullPaper: boolean }) {
  const calls: Call[] = [];
  const send = (req: TranslateRequest) =>
    new Promise<TranslateResponse>((resolve) => {
      calls.push({
        req,
        resolve: () =>
          resolve({
            results: req.blocks.map((b) => ({
              key: b.key,
              status: "ok" as const,
              text: `T:${b.text}`,
              model: "m",
              stale_terminology: false,
            })),
          }),
      });
    });
  const q = createTranslationQueue({
    paperId: 1,
    language: "日本語",
    fullPaper: opts.fullPaper,
    send,
  });
  const keys = Array.from({ length: n }, (_, i) => `h:${i}`);
  q.setBlocks(keys, Object.fromEntries(keys.map((k) => [k, `x${k}`])));
  return { q, calls, keys };
}

const sentKeys = (calls: Call[]) => calls.flatMap((c) => c.req.blocks.map((b) => b.key));
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("translation queue", () => {
  it("sends nothing until blocks are visible or look-ahead exists", () => {
    const { calls } = setup(100, { fullPaper: false });
    expect(calls.length).toBe(0);
  });

  it("sends visible blocks first, in batches of adjacent keys", () => {
    const { q, calls } = setup(100, { fullPaper: false });
    q.setVisible(["h:10", "h:11", "h:12", "h:13", "h:14", "h:15", "h:16"]);
    expect(calls[0]!.req.blocks.map((b) => b.key)).toEqual([
      "h:10", "h:11", "h:12", "h:13", "h:14",
    ]);
    expect(calls[1]!.req.blocks.map((b) => b.key)).toEqual(["h:15", "h:16", "h:17", "h:18", "h:19"]);
    expect(calls[0]!.req.paper_content_id).toBe(1);
    expect(calls[0]!.req.language).toBe("日本語");
  });

  it("caps in-flight requests at 2 and resumes as they finish", async () => {
    const { q, calls } = setup(100, { fullPaper: false });
    q.setVisible(["h:0"]);
    expect(calls.length).toBe(2);
    calls[0]!.resolve();
    await flush();
    expect(calls.length).toBe(3);
  });

  it("never sends past the look-ahead window when fullPaper is false", async () => {
    const { q, calls } = setup(100, { fullPaper: false });
    q.setVisible(["h:0", "h:1"]);
    for (let i = 0; i < 20; i++) {
      calls.forEach((c) => c.resolve());
      await flush();
    }
    const sent = sentKeys(calls).map((k) => Number(k.slice(2)));
    expect(Math.max(...sent)).toBe(31); // last visible (1) + 30
    expect(new Set(sent).size).toBe(sent.length);
  });

  it("covers the whole paper when fullPaper is true", async () => {
    const { q, calls } = setup(40, { fullPaper: true });
    q.setVisible(["h:0"]);
    for (let i = 0; i < 20; i++) {
      calls.forEach((c) => c.resolve());
      await flush();
    }
    expect(new Set(sentKeys(calls)).size).toBe(40);
  });

  it("re-prioritizes unsent work after a scroll", () => {
    const { q, calls } = setup(100, { fullPaper: false });
    q.setVisible(["h:0"]);
    expect(calls.length).toBe(2); // h:0-4, h:5-9 in flight
    q.setVisible(["h:60"]);
    calls[0]!.resolve();
    return flush().then(() => {
      expect(calls[2]!.req.blocks[0]!.key).toBe("h:60");
      const sent = sentKeys(calls);
      expect(new Set(sent).size).toBe(sent.length);
    });
  });

  it("reports results and stops after dispose", async () => {
    const { q, calls } = setup(100, { fullPaper: false });
    const got: string[] = [];
    q.onResult((r) => got.push(r.key));
    q.setVisible(["h:0"]);
    calls[0]!.resolve();
    await flush();
    expect(got).toEqual(["h:0", "h:1", "h:2", "h:3", "h:4"]);
    q.dispose();
    calls[1]!.resolve();
    await flush();
    expect(got.length).toBe(5);
    expect(calls.length).toBe(3);
  });

  it("emits unavailable on a rejected send, releases the slot, retry re-sends", async () => {
    const calls: TranslateRequest[] = [];
    let fail = true;
    const send = (req: TranslateRequest) => {
      calls.push(req);
      if (fail) return Promise.reject(new Error("boom"));
      return Promise.resolve({
        results: req.blocks.map((b) => ({
          key: b.key, status: "ok" as const, text: "t", model: "m", stale_terminology: false,
        })),
      });
    };
    const q = createTranslationQueue({ paperId: 1, language: "x", fullPaper: false, send, maxInFlight: 1 });
    const keys = Array.from({ length: 12 }, (_, i) => `h:${i}`);
    q.setBlocks(keys, Object.fromEntries(keys.map((k) => [k, "t"])));
    const got: [string, string][] = [];
    q.onResult((r) => got.push([r.key, r.status]));
    q.setVisible(["h:0"]);
    await flush();
    await flush();
    const first = calls[0]!.blocks.map((b) => b.key);
    expect(first).toEqual(["h:0", "h:1", "h:2", "h:3", "h:4"]);
    expect(got.filter(([, s]) => s === "unavailable").map(([k]) => k)).toEqual(
      expect.arrayContaining(first),
    );
    expect(calls.length).toBeGreaterThan(1);
    const all = calls.flatMap((c) => c.blocks.map((b) => b.key));
    expect(new Set(all).size).toBe(all.length);
    const before = calls.length;
    fail = false;
    q.retry(first);
    await flush();
    expect(calls.length).toBeGreaterThan(before);
    expect(calls.slice(before).flatMap((c) => c.blocks.map((b) => b.key))).toEqual(
      expect.arrayContaining(first),
    );
  });

  it("emits unavailable for keys the server omits, and survives a throwing listener", async () => {
    const send = (req: TranslateRequest) =>
      Promise.resolve({
        results: [{ key: req.blocks[0]!.key, status: "ok" as const, text: "t", model: "m", stale_terminology: false }],
      });
    const q = createTranslationQueue({ paperId: 1, language: "x", fullPaper: false, send, maxInFlight: 1, batchSize: 2 });
    q.setBlocks(["h:0", "h:1"], { "h:0": "a", "h:1": "b" });
    const got: [string, string][] = [];
    q.onResult(() => {
      throw new Error("listener");
    });
    q.onResult((r) => got.push([r.key, r.status]));
    q.setVisible(["h:0", "h:1"]);
    await flush();
    expect(got).toEqual([["h:0", "ok"], ["h:1", "unavailable"]]);
  });
});

describe("translation queue: retranslate (force)", () => {
  type R = { key: string; status: "ok" | "unavailable"; text: string | null; stale: boolean };
  function forceSetup(reply: (req: TranslateRequest) => R[] | Error) {
    const calls: TranslateRequest[] = [];
    const send = (req: TranslateRequest) => {
      calls.push(req);
      const r = reply(req);
      if (r instanceof Error) return Promise.reject(r);
      return Promise.resolve({
        results: r.map((x) => ({
          key: x.key, status: x.status, text: x.text, model: "m", stale_terminology: x.stale,
        })),
      });
    };
    const q = createTranslationQueue({ paperId: 1, language: "x", fullPaper: false, send });
    const keys = Array.from({ length: 4 }, (_, i) => `h:${i}`);
    q.setBlocks(keys, Object.fromEntries(keys.map((k) => [k, `src ${k}`])));
    const got: TranslateResult[] = [];
    q.onResult((r) => got.push(r));
    return { q, calls, got };
  }

  it("re-sends the given keys with force: true, apart from unforced work", async () => {
    let mode: "stale" | "fresh" = "stale";
    const { q, calls, got } = forceSetup((req) =>
      req.blocks.map((b) => ({ key: b.key, status: "ok", text: `${mode}:${b.key}`, stale: mode === "stale" })),
    );
    q.setVisible(["h:0", "h:1"]);
    await flush();
    expect(calls).toHaveLength(1);
    expect(calls[0]!.force).toBeUndefined();
    mode = "fresh";
    q.retranslate(["h:0", "h:1"]);
    await flush();
    expect(calls).toHaveLength(2);
    expect(calls[1]!.force).toBe(true);
    expect(calls[1]!.blocks.map((b) => b.key)).toEqual(["h:0", "h:1"]);
    expect(got.slice(-2).map((r) => [r.key, r.text, r.stale_terminology])).toEqual([
      ["h:0", "fresh:h:0", false],
      ["h:1", "fresh:h:1", false],
    ]);
    // Force is one-shot: a later retry of the same key is a plain request.
    q.retry(["h:0"]);
    await flush();
    expect(calls[2]!.force).toBeUndefined();
  });

  it("a failed forced request keeps the last good text, flagged stale", async () => {
    let fail = false;
    const { q, got } = forceSetup((req) =>
      fail
        ? new Error("down")
        : req.blocks.map((b) => ({ key: b.key, status: "ok", text: `old:${b.key}`, stale: true })),
    );
    q.setVisible(["h:0"]);
    await flush();
    fail = true;
    q.retranslate(["h:0"]);
    await flush();
    const last = got.filter((r) => r.key === "h:0").at(-1)!;
    expect(last.status).toBe("ok");
    expect(last.text).toBe("old:h:0");
    expect(last.stale_terminology).toBe(true);
  });

  it("an unavailable forced result also keeps the last good text", async () => {
    let unavailable = false;
    const { q, got } = forceSetup((req) =>
      req.blocks.map((b) =>
        unavailable
          ? { key: b.key, status: "unavailable", text: null, stale: false }
          : { key: b.key, status: "ok", text: `old:${b.key}`, stale: true },
      ),
    );
    q.setVisible(["h:0"]);
    await flush();
    unavailable = true;
    q.retranslate(["h:0"]);
    await flush();
    const last = got.filter((r) => r.key === "h:0").at(-1)!;
    expect([last.status, last.text, last.stale_terminology]).toEqual(["ok", "old:h:0", true]);
  });
});

describe("translation queue: late unforced replies", () => {
  it("drops an unforced reply that lands after a forced one for the same key", async () => {
    const pending: { req: TranslateRequest; resolve: () => void }[] = [];
    const send = (req: TranslateRequest) =>
      new Promise<TranslateResponse>((resolve) => {
        pending.push({
          req,
          resolve: () =>
            resolve({
              results: req.blocks.map((b) => ({
                key: b.key,
                status: "ok" as const,
                text: `${req.force ? "new" : "old"}:${b.key}`,
                model: "m",
                stale_terminology: !req.force,
              })),
            }),
        });
      });
    const q = createTranslationQueue({ paperId: 1, language: "x", fullPaper: false, send });
    q.setBlocks(["h:0"], { "h:0": "a" });
    const got: TranslateResult[] = [];
    q.onResult((r) => got.push(r));
    q.setVisible(["h:0"]);
    expect(pending).toHaveLength(1);
    q.retranslate(["h:0"]);
    expect(pending).toHaveLength(2);
    expect(pending[1]!.req.force).toBe(true);
    pending[1]!.resolve();
    await flush();
    pending[0]!.resolve(); // the late, unforced reply
    await flush();
    expect(got.map((r) => r.text)).toEqual(["new:h:0"]);
    // Once the forced reply has landed, unforced work for the key flows again.
    q.retry(["h:0"]);
    pending[2]!.resolve();
    await flush();
    expect(got.at(-1)!.text).toBe("old:h:0");
  });
});

describe("translation queue full-paper back-off", () => {
  function failing(n: number) {
    let fail = true;
    const sent: TranslateRequest[] = [];
    const send = (req: TranslateRequest): Promise<TranslateResponse> => {
      sent.push(req);
      if (fail) return Promise.reject(new Error("provider down"));
      return Promise.resolve({
        results: req.blocks.map((b) => ({
          key: b.key, status: "ok" as const, text: "t", model: "m", stale_terminology: false,
        })),
      });
    };
    const q = createTranslationQueue({ paperId: 1, language: "ja", fullPaper: true, send });
    const keys = Array.from({ length: n }, (_, i) => `h:${i}`);
    q.setBlocks(keys, Object.fromEntries(keys.map((k) => [k, `x${k}`])));
    const settle = async () => {
      for (let i = 0; i < 60; i++) await flush();
    };
    const max = () => Math.max(...sent.flatMap((r) => r.blocks.map((b) => Number(b.key.slice(2)))));
    return { q, sent, settle, max, recover: () => { fail = false; } };
  }

  it("stops the background part after 3 all-unavailable batches", async () => {
    const { q, settle, max } = failing(200);
    q.setVisible(["h:0"]);
    await settle();
    expect(max()).toBe(30); // visible + look-ahead only
  });

  it("resumes on a visible-set change and on retry()", async () => {
    const { q, sent, settle, max, recover } = failing(200);
    q.setVisible(["h:0"]);
    await settle();
    q.setVisible(["h:100"]);
    await settle();
    expect(sent.some((r) => r.blocks[0]!.key === "h:100")).toBe(true);
    const before = sent.length;
    recover();
    q.retry([]);
    await settle();
    expect(sent.length).toBeGreaterThan(before);
    expect(max()).toBe(199);
  });
});
