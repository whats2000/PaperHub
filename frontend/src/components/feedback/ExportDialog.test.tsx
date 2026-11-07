import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

import { toast } from "sonner";

import { ExportDialog } from "@/components/feedback/ExportDialog";
import { API_BASE_URL } from "@/lib/api";

let previewUrls: URL[] = [];
let includesPrivate = true;
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let previewDelay: Record<string, number> = {};
let exportFails = false;
let exportDelay = 0;
let exportBody: Record<string, unknown> | null = null;

const server = setupServer(
  http.get(`${API_BASE_URL}/feedback/export/preview`, async ({ request }) => {
    const u = new URL(request.url);
    previewUrls.push(u);
    const d = previewDelay[u.searchParams.get("model") ?? ""] ?? 0;
    if (d) await new Promise((r) => setTimeout(r, d));
    if (u.searchParams.get("model") === "slow") {
      return HttpResponse.json({ sft: 999, dpo: 9, includes_private: includesPrivate });
    }
    return HttpResponse.json({ sft: 145, dpo: 11, includes_private: includesPrivate });
  }),
  http.post(`${API_BASE_URL}/feedback/export`, async ({ request }) => {
    exportBody = (await request.json()) as Record<string, unknown>;
    if (exportDelay) await new Promise((r) => setTimeout(r, exportDelay));
    if (exportFails) return new HttpResponse("boom", { status: 500 });
    return HttpResponse.json({
      dir: "20261105-1530",
      files: ["sft.jsonl", "dpo.jsonl", "README.md"],
      sft: 145,
      dpo: 11,
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  previewUrls = [];
  includesPrivate = true;
  exportBody = null;
  previewDelay = {};
  exportFails = false;
  exportDelay = 0;
  vi.mocked(toast.error).mockClear();
});
afterEach(() => {
  server.resetHandlers();
  vi.useRealTimers();
});

function Harness() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button onClick={() => setOpen(true)}>reopen</button>
      <ExportDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

async function settle(ms = 350) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("ExportDialog", () => {
  it("debounces the preview and shows the counts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Harness />);
    expect(previewUrls).toHaveLength(0);
    await settle(200);
    expect(previewUrls).toHaveLength(0);
    await settle(150);
    expect(previewUrls).toHaveLength(1);
    expect(previewUrls[0]!.searchParams.get("include_private")).toBe("true");
    expect(previewUrls[0]!.searchParams.get("cited_only")).toBe("false");
    expect(await screen.findByText("145")).toBeInTheDocument();
    expect(screen.getByText("11")).toBeInTheDocument();
  });

  it("refetches the preview when an option is toggled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    await settle();
    expect(previewUrls).toHaveLength(1);
    await user.click(screen.getByRole("switch", { name: /cited/i }));
    await settle();
    expect(previewUrls).toHaveLength(2);
    expect(previewUrls[1]!.searchParams.get("cited_only")).toBe("true");
    await user.click(screen.getByRole("switch", { name: /private/i }));
    await settle();
    expect(previewUrls[2]!.searchParams.get("include_private")).toBe("false");
  });

  it("shows the private-documents warning only when includes_private is true", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    await settle();
    expect(await screen.findByText(/text of private documents/i)).toBeInTheDocument();
    includesPrivate = false;
    await user.click(screen.getByRole("switch", { name: /private/i }));
    await settle();
    expect(screen.queryByText(/text of private documents/i)).toBeNull();
  });

  it("exports and lists the files as links to the backend", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    await settle();
    await user.click(screen.getByRole("button", { name: /^export$/i }));
    await settle(50);
    const link = await screen.findByRole("link", { name: "sft.jsonl" });
    expect(link).toHaveAttribute(
      "href",
      `${API_BASE_URL}/feedback/exports/20261105-1530/sft.jsonl`,
    );
    expect(screen.getByRole("link", { name: "dpo.jsonl" })).toBeInTheDocument();
    expect(exportBody).toMatchObject({ include_private: true, cited_only: false });
  });

  it("animates out: still mounted right after close, gone after 150 ms", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    await settle();
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    const dlg = screen.getByRole("dialog");
    expect(dlg).toHaveAttribute("data-state", "closed");
    await settle(160);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("sends the date range and model", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    await settle();
    await user.type(screen.getByLabelText("From"), "2026-10-01");
    await user.type(screen.getByLabelText("To"), "2026-10-31");
    await user.type(screen.getByLabelText("Answer model"), "gpt-x");
    await settle();
    const u = previewUrls[previewUrls.length - 1]!;
    expect(u.searchParams.get("since")).toBe("2026-10-01");
    expect(u.searchParams.get("until")).toBe("2026-10-31");
    expect(u.searchParams.get("model")).toBe("gpt-x");
    await user.click(screen.getByRole("button", { name: /^export$/i }));
    await settle(50);
    expect(exportBody).toMatchObject({ since: "2026-10-01", until: "2026-10-31", model: "gpt-x" });
  });

  it("does not let an older preview response win over a newer one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    previewDelay = { slow: 2000 };
    render(<Harness />);
    await settle();
    await user.type(screen.getByLabelText("Answer model"), "slow");
    await settle(); // request for "slow" is in flight (999)
    await user.clear(screen.getByLabelText("Answer model"));
    await settle(); // newer request resolves first (145)
    await settle(2500); // the slow one lands late
    expect(screen.getByText("145")).toBeInTheDocument();
    expect(screen.queryByText("999")).toBeNull();
  });

  it("closes on Escape with the exit animation", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    await settle();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "closed");
    await settle(160);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("disables Export while exporting and ignores a result that arrives after an option change", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    exportDelay = 1000;
    render(<Harness />);
    await settle();
    await user.click(screen.getByRole("button", { name: /^export$/i }));
    await settle(50);
    expect(screen.getByRole("button", { name: /^export$/i })).toBeDisabled();
    await user.click(screen.getByRole("switch", { name: /cited/i }));
    await settle(1500);
    expect(screen.queryByRole("link", { name: "sft.jsonl" })).toBeNull();
    expect(screen.getByRole("button", { name: /^export$/i })).toBeEnabled();
  });

  it("toasts when the export fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    exportFails = true;
    render(<Harness />);
    await settle();
    await user.click(screen.getByRole("button", { name: /^export$/i }));
    await settle(50);
    expect(toast.error).toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "sft.jsonl" })).toBeNull();
  });

  it("starts clean after a close and reopen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<Harness />);
    await settle();
    await user.click(screen.getByRole("button", { name: /^export$/i }));
    await settle(50);
    expect(screen.getByRole("link", { name: "sft.jsonl" })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: /close/i }).at(-1)!);
    await settle(200);
    await user.click(screen.getByRole("button", { name: "reopen" }));
    expect(screen.queryByRole("link", { name: "sft.jsonl" })).toBeNull();
    expect(screen.queryByText("145")).toBeNull();
  });
});
