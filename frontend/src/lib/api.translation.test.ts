import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { getPdfParagraphs, getTranslateConfig, translateBlocks } from "@/lib/api";

const server = setupServer(
  http.post("http://localhost:8000/translations", async ({ request }) => {
    const body = (await request.json()) as { blocks: { key: string }[] };
    return HttpResponse.json({
      results: body.blocks.map((b) => ({
        key: b.key, status: "ok", text: "t", model: "m", stale_terminology: false,
      })),
    });
  }),
  http.get("http://localhost:8000/translations/config", () =>
    HttpResponse.json({ full_paper: true }),
  ),
  http.get("http://localhost:8000/papers/content/5/paragraphs", () =>
    HttpResponse.json({ status: "no_blocks", paragraphs: [] }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("translation api", () => {
  it("posts blocks and reads config + paragraphs", async () => {
    const r = await translateBlocks({
      paper_content_id: 5, language: "日本語", blocks: [{ key: "h:0", text: "x" }],
    });
    expect(r.results[0]!.key).toBe("h:0");
    expect((await getTranslateConfig()).full_paper).toBe(true);
    expect((await getPdfParagraphs(5)).status).toBe("no_blocks");
  });
});
