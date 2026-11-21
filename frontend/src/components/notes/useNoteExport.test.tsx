import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { useNoteExport } from "@/components/notes/useNoteExport";
import { exportFileName } from "@/lib/api";
import { useNotesStore } from "@/store/notes";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toastError } }));

const BASE = "http://localhost:8000";
let caps: Record<string, unknown> = { md: true, docx: true, docx_reason: null };
let requested: URLSearchParams[] = [];

const server = setupServer(
  http.get(`${BASE}/notes/export/capabilities`, () => HttpResponse.json(caps)),
  http.get(`${BASE}/notes/7/export`, ({ request }) => {
    const params = new URL(request.url).searchParams;
    requested.push(params);
    const format = params.get("format");
    return new HttpResponse(new Blob(["zip"]), {
      headers: {
        "Content-Type": format === "md" ? "application/zip" : "application/octet-stream",
        "Content-Disposition":
          "attachment; filename=\"note-notes.zip\"; filename*=UTF-8''%E6%B3%A8%E6%84%8F-notes.zip",
      },
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
  toastError.mockReset();
});
afterAll(() => server.close());

let clicked: string[];
const revoke = vi.fn();
beforeEach(() => {
  caps = { md: true, docx: true, docx_reason: null };
  requested = [];
  useNotesStore.setState({ tableStyle: "threeLine" });
  clicked = [];
  URL.createObjectURL = vi.fn(() => "blob:x");
  revoke.mockReset();
  URL.revokeObjectURL = revoke;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push(this.download);
  });
});

describe("exportFileName", () => {
  it("prefers the UTF-8 name, then the ASCII one, then the fallback", () => {
    expect(exportFileName("attachment; filename=\"a-notes.zip\"; filename*=UTF-8''%E7%AD%86-notes.zip", "x")).toBe(
      "筆-notes.zip",
    );
    expect(exportFileName('attachment; filename="a-notes.zip"', "x")).toBe("a-notes.zip");
    expect(exportFileName("", "x.zip")).toBe("x.zip");
    expect(exportFileName("attachment; filename*=UTF-8''%E0%A4", "x.zip")).toBe("x.zip");
  });
});

describe("useNoteExport", () => {
  it("downloads under the server's name and revokes the URL after a tick", async () => {
    const { result } = renderHook(() => useNoteExport(7));
    let settled = false;
    let done!: Promise<void>;
    act(() => {
      done = result.current.onExport("md").then(() => {
        settled = true;
      });
    });
    // The export (and so the item's busy state) lasts until the download is saved.
    expect(settled).toBe(false);
    await act(() => done);
    expect(clicked).toEqual(["注意-notes.zip"]);
    expect(revoke).not.toHaveBeenCalled();
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:x"));
    expect(toastError).not.toHaveBeenCalled();
  });

  it("asks for the tables in the viewer's table style", async () => {
    const { result } = renderHook(() => useNoteExport(7));
    await act(() => result.current.onExport("docx"));
    act(() => {
      useNotesStore.setState({ tableStyle: "grid" });
    });
    await act(() => result.current.onExport("docx"));
    expect(requested.map((p) => [p.get("format"), p.get("table_style")])).toEqual([
      ["docx", "threeLine"],
      ["docx", "grid"],
    ]);
  });

  it("takes Word's reason from the capabilities", async () => {
    caps = { md: true, docx: false, docx_reason: "pandoc_too_old" };
    const { result } = renderHook(() => useNoteExport(7));
    await waitFor(() => expect(result.current.docxReason).toBe("pandoc_too_old"));
  });

  it("a 409 pandoc_missing disables Word and says how to install pandoc", async () => {
    server.use(
      http.get(`${BASE}/notes/7/export`, () =>
        HttpResponse.json({ detail: "pandoc_missing" }, { status: 409 }),
      ),
    );
    const { result } = renderHook(() => useNoteExport(7));
    await waitFor(() => expect(result.current.docxReason).toBeNull());
    await act(() => result.current.onExport("docx"));
    expect(result.current.docxReason).toBe("pandoc_missing");
    expect(toastError).toHaveBeenCalledWith(expect.stringContaining("winget install pandoc"));
    expect(clicked).toEqual([]);
  });

  it("any other failure shows an error toast", async () => {
    server.use(http.get(`${BASE}/notes/7/export`, () => new HttpResponse("boom", { status: 500 })));
    const { result } = renderHook(() => useNoteExport(7));
    await act(() => result.current.onExport("md"));
    expect(toastError).toHaveBeenCalledWith("Couldn't export the note");
    expect(clicked).toEqual([]);
  });
});
