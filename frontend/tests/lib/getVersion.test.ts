import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { getVersion, API_BASE_URL } from "@/lib/api";
import type { VersionInfo } from "@/types/domain";

const mockVersionInfo: VersionInfo = {
  current: "2.37.0",
  latest: "2.38.0",
  update_available: true,
  html_url: "https://github.com/example/paperhub/releases/tag/v2.38.0",
  checked_at: "2026-06-18T00:00:00Z",
};

const server = setupServer(
  http.get(`${API_BASE_URL}/version`, () => HttpResponse.json(mockVersionInfo)),
);

describe("getVersion", () => {
  beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());

  it("resolves with current version and update_available flag", async () => {
    const info = await getVersion();
    expect(info.current).toBe("2.37.0");
    expect(info.update_available).toBe(true);
    expect(info.latest).toBe("2.38.0");
  });

  it("resolves when no update is available", async () => {
    server.use(
      http.get(`${API_BASE_URL}/version`, () =>
        HttpResponse.json({
          current: "2.37.0",
          latest: "2.37.0",
          update_available: false,
          html_url: null,
          checked_at: "2026-06-18T00:00:00Z",
        } satisfies VersionInfo),
      ),
    );
    const info = await getVersion();
    expect(info.current).toBe("2.37.0");
    expect(info.update_available).toBe(false);
    expect(info.html_url).toBeNull();
  });
});
