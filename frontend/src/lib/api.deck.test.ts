import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { getDeckIfAny } from "@/lib/api";

let seenOptional: string | null = null;
const server = setupServer(
  http.get("http://localhost:8000/sessions/:id/deck", ({ request, params }) => {
    seenOptional = new URL(request.url).searchParams.get("optional");
    if (params.id === "3") return HttpResponse.json(null);
    return HttpResponse.json({ deck_id: "d1", session_id: 4, page_count: 2 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("getDeckIfAny", () => {
  it("asks for the optional lookup and returns null for a deckless chat", async () => {
    expect(await getDeckIfAny(3)).toBeNull();
    expect(seenOptional).toBe("1");
  });

  it("returns the deck metadata when the chat has one", async () => {
    expect(await getDeckIfAny(4)).toMatchObject({ deck_id: "d1", page_count: 2 });
  });
});
