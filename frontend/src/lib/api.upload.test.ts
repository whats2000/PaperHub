import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadPdf } from "@/lib/api";

function stubFetch() {
  const fetchMock = vi.fn(() => Promise.resolve(
    new Response(
      JSON.stringify({
        papers_id: 1,
        paper_content_id: 2,
        cache_hit: false,
        title: "T",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  ));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentForm(fetchMock: ReturnType<typeof stubFetch>): FormData {
  const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
  return calls[0]![1].body as FormData;
}

const pdf = () => new File([new Uint8Array([0x25])], "p.pdf");

afterEach(() => vi.unstubAllGlobals());

describe("uploadPdf private flag", () => {
  it("sends private=true only when asked", async () => {
    const fetchMock = stubFetch();
    await uploadPdf(5, pdf(), { private: true });
    const form = sentForm(fetchMock);
    expect(form.get("private")).toBe("true");
    expect(form.get("session_id")).toBe("5");
  });

  it("omits the private key without opts or when false", async () => {
    let fetchMock = stubFetch();
    await uploadPdf(5, pdf());
    expect(sentForm(fetchMock).has("private")).toBe(false);

    fetchMock = stubFetch();
    await uploadPdf(5, pdf(), { private: false });
    expect(sentForm(fetchMock).has("private")).toBe(false);
  });
});
