import { Suspense } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { retryableLazy } from "@/lib/retryableLazy";

import { PanelLoadBoundary } from "./PanelLoadBoundary";

function Panel({ label }: { label: string }) {
  return <p>{label}</p>;
}

describe("PanelLoadBoundary", () => {
  let errorSpy: { mockRestore: () => void };
  beforeEach(() => {
    // React logs caught render errors; keep the test output readable.
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => errorSpy.mockRestore());

  it("keeps the rest of the app mounted when a lazy panel fails to load", async () => {
    const load = vi
      .fn()
      .mockRejectedValue(new TypeError("Failed to fetch dynamically imported module"));
    const LazyPanel = retryableLazy<typeof Panel>(load);
    render(
      <div>
        <p>chat thread</p>
        <PanelLoadBoundary onRetry={LazyPanel.reset}>
          <Suspense fallback={null}>
            <LazyPanel label="slides" />
          </Suspense>
        </PanelLoadBoundary>
      </div>,
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("chat thread")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reload/i })).toBeInTheDocument();
  });

  it("re-runs the import on Try again and shows the panel once it loads", async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch dynamically imported module"))
      .mockResolvedValue({ default: Panel });
    const LazyPanel = retryableLazy<typeof Panel>(load);
    render(
      <PanelLoadBoundary onRetry={LazyPanel.reset}>
        <Suspense fallback={null}>
          <LazyPanel label="slides loaded" />
        </Suspense>
      </PanelLoadBoundary>,
    );
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(screen.getByText("slides loaded")).toBeInTheDocument());
    expect(load).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
