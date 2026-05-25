import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { getDeck, deckPdfUrl } from "@/lib/api";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("decks api", () => {
  it("getDeck returns metadata", async () => {
    server.use(
      http.get("http://localhost:8000/sessions/7/deck", () =>
        HttpResponse.json({ deck_id: 1, session_id: 7, page_count: 5, status: "ok",
          theme: "metropolis", plan: {}, speaker_notes: { "1": "n" },
          contributing_paper_ids: [], updated_at: "" })),
    );
    const d = await getDeck(7);
    expect(d.page_count).toBe(5);
    expect(d.speaker_notes["1"]).toBe("n");
  });

  it("deckPdfUrl builds the right URL", () => {
    expect(deckPdfUrl(7)).toBe("http://localhost:8000/sessions/7/deck/pdf");
  });
});
