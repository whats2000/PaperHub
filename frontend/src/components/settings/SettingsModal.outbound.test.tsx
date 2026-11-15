import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { API_BASE_URL } from "../../lib/api";
import { useSettingsStore } from "../../store/settings";
import { SettingsModal } from "./SettingsModal";

const field = (key: string, label: string) => ({
  key,
  label,
  type: "string",
  value: "x",
  secret: false,
  restart_required: false,
  locked: false,
});

const server = setupServer(
  http.get(`${API_BASE_URL}/settings`, () =>
    HttpResponse.json({
      categories: [
        { key: "privacy_outbound", label: "Privacy & outbound", fields: [] },
        { key: "models_providers", label: "Models & providers", fields: [field("A", "Alpha")] },
      ],
    }),
  ),
  http.get(`${API_BASE_URL}/outbound/log`, () => HttpResponse.json([])),
  http.get(`${API_BASE_URL}/outbound/held`, () => HttpResponse.json([])),
);

describe("SettingsModal outbound log", () => {
  beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
  beforeEach(() => {
    useSettingsStore.setState({
      isOpen: true,
      config: null,
      restartPending: [],
      readiness: null,
      destinations: [],
      trustedStatus: null,
      target: { section: null, tab: "all", nonce: 0 },
    });
  });

  it("has an Outbound log nav entry that shows the panel", async () => {
    render(<SettingsModal />);
    await userEvent.click(await screen.findByRole("button", { name: "Outbound log" }));
    expect(await screen.findByRole("tab", { name: "Everything sent" })).toBeInTheDocument();
  });

  it("the Privacy pane button opens the log", async () => {
    render(<SettingsModal />);
    await userEvent.click(await screen.findByRole("button", { name: "Open the outbound log" }));
    expect(await screen.findByRole("tab", { name: "Everything sent" })).toBeInTheDocument();
  });

  it("openAt lands on Outbound log > Held", async () => {
    render(<SettingsModal />);
    await screen.findByRole("button", { name: "Outbound log" });
    useSettingsStore.getState().openAt("outbound_log", "held");
    const tab = await screen.findByRole("tab", { name: /Held/ });
    expect(tab).toHaveAttribute("aria-selected", "true");
  });

  it("the nav entry and Privacy button open the all tab even after a Held open", async () => {
    render(<SettingsModal />);
    await screen.findByRole("button", { name: "Outbound log" });
    useSettingsStore.getState().openAt("outbound_log", "held");
    await screen.findByRole("tab", { name: /Held/ });
    await userEvent.click(screen.getByRole("button", { name: "Privacy & outbound" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open the outbound log" }));
    expect(await screen.findByRole("tab", { name: "Everything sent" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("button", { name: "Models & providers" }));
    await userEvent.click(screen.getByRole("button", { name: "Outbound log" }));
    expect(await screen.findByRole("tab", { name: "Everything sent" })).toHaveAttribute("aria-selected", "true");
  });
});
